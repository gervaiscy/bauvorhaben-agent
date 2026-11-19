"""Durable USD reservations bound every attempt, including failures and reasoning."""
import copy
from decimal import Decimal, ROUND_UP
import fcntl
import hashlib
import json
from pathlib import Path

from .trace import ModelRequestFailed
from .schemas import generation_for, response_schema_for

PRICES = {'model': 'gemini-3.5-flash', 'location': 'global',
          'input_usd_per_million': '1.50', 'output_usd_per_million': '9.00',
          'source': 'https://cloud.google.com/model-garden/pricing', 'verified_on': '2026-11-17'}

PRICE_POLICIES = {(PRICES['model'], PRICES['location']): PRICES,
    ('gpt-6.1-sol', 'openai'): {
        'model': 'gpt-6.1-sol', 'location': 'openai',
        'input_usd_per_million': '2.50', 'output_usd_per_million': '10.00',
        'long_context_threshold': 272000,
        'long_context_input_usd_per_million': '5.00',
        'long_context_output_usd_per_million': '15.00',
        'source': 'https://developers.openai.com/api/docs/models/gpt-6.1-sol',
        'verified_on': '2026-11-19',
        'note': 'Conservative bound: all input charged at the cache-write rate; output includes reasoning.'},
    ('gemini-3.1-pro-preview', 'global'): {
        'model': 'gemini-3.1-pro-preview', 'location': 'global',
        'input_usd_per_million': '2.00', 'output_usd_per_million': '12.00',
        'long_context_threshold': 200000,
        'long_context_input_usd_per_million': '4.00',
        'long_context_output_usd_per_million': '18.00',
        'source': 'https://cloud.google.com/model-garden/pricing', 'verified_on': '2026-11-17'}}



def money(value):
    return str(Decimal(value).quantize(Decimal('0.000000001'), rounding=ROUND_UP))


def price(inputs, outputs, policy=PRICES):
    prefix = 'long_context_' if inputs > policy.get('long_context_threshold', float('inf')) else ''
    return (Decimal(inputs) * Decimal(policy[prefix + 'input_usd_per_million'])
            + Decimal(outputs) * Decimal(policy[prefix + 'output_usd_per_million'])) / 1_000_000


class CostLimitExceeded(ModelRequestFailed):
    def __init__(self, reason='usd_limit_exhausted'):
        self.attempts = [{'error_type': 'CostLimitExceeded', 'reason': reason}]
        message = ('Spend ledger is closed; no provider call made' if reason == 'ledger_closed'
                   else 'USD ceiling would be exceeded; no provider call made')
        RuntimeError.__init__(self, message)


class SpendLedger:
    def __init__(self, path, limit_usd, model='gemini-3.5-flash', location='global'):
        self.path = Path(path)
        self.limit = Decimal(str(limit_usd))
        if not self.limit.is_finite() or self.limit <= 0:
            raise ValueError('USD limit must be positive and finite')
        if (model, location) not in PRICE_POLICIES:
            raise ValueError('No verified spend policy for this model/location')
        self.prices = PRICE_POLICIES[(model, location)]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._update(lambda ledger: None)

    def _update(self, operation):
        with self.path.with_suffix(self.path.suffix + '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = json.loads(self.path.read_text()) if self.path.exists() else {
                'limit_usd': money(self.limit), 'prices': self.prices, 'attempts': []}
            if Decimal(state['limit_usd']) != self.limit or state['prices'] != self.prices:
                raise ValueError('Spend ledger policy changed; reuse its original limit and prices')
            value = operation(state)
            bound = sum(Decimal(a.get('accounted_usd', a['reserved_usd'])) for a in state['attempts'])
            state.update(accounted_upper_bound_usd=money(bound),
                         remaining_usd=money(self.limit - bound),
                         reported_token_cost_usd=money(sum(Decimal(a['accounted_usd']) for a in state['attempts']
                            if a.get('usage_complete'))),
                         note='List-price token estimate, not a billing invoice. Unknown usage retains its full reservation.')
            temporary = self.path.with_suffix(self.path.suffix + '.tmp')
            temporary.write_text(json.dumps(state, indent=2) + '\n')
            temporary.replace(self.path)
            return value

    def reserve(self, prompt):
        phase = prompt.split('.', 1)[0]
        config = generation_for(phase)
        # For text-only input, byte count is deliberately much larger than token
        # count. Include the response schema and a fixed provider-envelope reserve.
        inputs = len(prompt.encode('utf-8')) + len(json.dumps(response_schema_for(prompt) or {}).encode()) + 8192
        outputs = config['max_output_tokens']  # Includes response AND thinking.
        maximum = Decimal(money(price(inputs, outputs, self.prices)))
        def append(state):
            if state.get('closed'):
                raise CostLimitExceeded('ledger_closed')
            used = sum(Decimal(a.get('accounted_usd', a['reserved_usd'])) for a in state['attempts'])
            if used + maximum > self.limit:
                raise CostLimitExceeded()
            index = len(state['attempts'])
            state['attempts'].append({'attempt': index + 1,
                'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
                'reserved_input_tokens': inputs, 'reserved_output_tokens': outputs,
                'reserved_usd': money(maximum), 'status': 'reserved'})
            return index
        return self._update(append)

    def close(self):
        """Freeze a completed allocation; retained unknown usage stays reserved."""
        def freeze(state):
            if any(a['status'] == 'reserved' for a in state['attempts']):
                raise ValueError('Cannot close a spend ledger with requests still in flight')
            state['closed'] = True
        self._update(freeze)

    def settle(self, index, diagnostics, succeeded):
        usage = (diagnostics or {}).get('usage_metadata') or {}
        if usage.get('provider') == 'openai':
            # OpenAI output_tokens already includes reasoning. Retain native
            # metadata in the attempt and never add reasoning tokens again.
            usage_counts = {'prompt_token_count': usage.get('input_tokens'),
                            'candidates_token_count': usage.get('output_tokens'),
                            'thoughts_token_count': 0}
        else:
            usage_counts = usage
        fields = ('prompt_token_count', 'candidates_token_count')
        complete = all(type(usage_counts.get(k)) is int and usage_counts[k] >= 0 for k in fields)
        thinking = usage_counts.get('thoughts_token_count')
        total = usage_counts.get('total_token_count')
        if complete and type(total) is int and total >= usage_counts['prompt_token_count'] + usage_counts['candidates_token_count']:
            residual = total - usage_counts['prompt_token_count'] - usage_counts['candidates_token_count']
            thinking = max(thinking, residual) if type(thinking) is int and thinking >= 0 else residual
        # Missing reasoning metadata is not evidence of zero billable reasoning.
        complete = complete and type(thinking) is int and thinking >= 0
        def apply(state):
            attempt = state['attempts'][index]
            attempt.update(status='completed' if succeeded else 'failed', usage=copy.deepcopy(usage),
                           usage_complete=complete)
            if complete:
                inputs = usage_counts['prompt_token_count']
                outputs = usage_counts['candidates_token_count'] + thinking
                if inputs > attempt['reserved_input_tokens'] or outputs > attempt['reserved_output_tokens']:
                    raise ValueError('Provider usage exceeded reserved token envelope; stop further spending')
                attempt['accounted_usd'] = money(price(inputs, outputs, self.prices))
        self._update(apply)


class SpendLimitedModel:
    def __init__(self, factory, ledger):
        self.factory, self.ledger, self.backend = factory, ledger, None
        self.last_raw_response = self.last_finish_reason = self.last_diagnostics = None

    def extract(self, prompt):
        self.last_raw_response = self.last_finish_reason = self.last_diagnostics = None
        index = self.ledger.reserve(prompt)
        success = False
        try:
            if self.backend is None:
                self.backend = self.factory()
            answer = self.backend.extract(prompt)
            success = True
            return answer
        finally:
            self.last_raw_response = getattr(self.backend, 'last_raw_response', None)
            self.last_finish_reason = getattr(self.backend, 'last_finish_reason', None)
            self.last_diagnostics = copy.deepcopy(getattr(self.backend, 'last_diagnostics', None))
            self.ledger.settle(index, self.last_diagnostics, success)
