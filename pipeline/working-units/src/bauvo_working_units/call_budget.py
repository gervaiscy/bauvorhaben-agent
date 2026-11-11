"""Explicit, shared provider-attempt budget; constructing a client is lazy."""
import copy
from .trace import ModelRequestFailed


class PlanOnlyStop(RuntimeError):
    """A known request is missing; stop planning before dependent stages."""


class CallLimitExceeded(ModelRequestFailed):
    def __init__(self, message):
        self.attempts = [{'error_type': 'CallLimitExceeded', 'reason': 'call_limit_exhausted',
                          'message': message}]
        RuntimeError.__init__(self, message)


class CallBudget:
    def __init__(self, limit=0):
        if type(limit) is not int or limit < 0:
            raise ValueError('New-call limit must be a nonnegative integer')
        self.limit, self.used = limit, 0

    @property
    def remaining(self):
        return self.limit - self.used

    def check(self, planned):
        if planned > self.remaining:
            raise CallLimitExceeded(f'Planned {planned} new provider calls; {self.remaining} remain '
                                   f'(limit={self.limit}, used={self.used}). No call made for this plan. '
                                   'Replay offline or explicitly increase --max-new-calls.')

    def consume(self):
        self.check(1)
        self.used += 1


class BudgetedModel:
    """Count actual attempts, including retries, before constructing/contacting a client."""
    def __init__(self, factory, budget):
        self.factory, self.budget, self.backend = factory, budget, None
        self.last_raw_response = self.last_finish_reason = self.last_diagnostics = None

    def extract(self, prompt):
        self.last_raw_response = self.last_finish_reason = self.last_diagnostics = None
        self.budget.consume()
        if self.backend is None:
            self.backend = self.factory()
        try:
            return self.backend.extract(prompt)
        finally:
            self.last_raw_response = getattr(self.backend, 'last_raw_response', None)
            self.last_finish_reason = getattr(self.backend, 'last_finish_reason', None)
            self.last_diagnostics = copy.deepcopy(getattr(self.backend, 'last_diagnostics', None))


def prepare_requests(model, prompts):
    """Pass a known stage plan through recording/retry wrappers before any paid work."""
    seen = set()
    while model is not None and id(model) not in seen:
        seen.add(id(model))
        if hasattr(model, 'prepare'):
            return model.prepare(prompts)
        model = getattr(model, '_model', getattr(model, 'model', None))


def add_call_arguments(parser):
    parser.add_argument('--max-new-calls', type=int, default=0,
                        help='Actual provider-attempt limit including retries; default 0 (offline)')
    parser.add_argument('--cache-dir', help='Immutable completed provider-response store')


def cached_backend(args, output, factory):
    from pathlib import Path
    from .cache import CachedModel
    output = Path(output)
    budget = CallBudget(args.max_new_calls)
    return CachedModel(BudgetedModel(factory, budget),
        args.cache_dir or output.parent/'provider-responses',
        {'project':args.project, 'location':args.location, 'model':args.model},
        budget=budget, plan_path=output.with_name(output.stem+'.request-plan.json'))


class FileCallBudget:
    """Expose the pipeline's process-shared ledger through the same budget interface."""
    def __init__(self, ledger):
        self.ledger = ledger

    @property
    def limit(self):
        return self.ledger.access()['limit']

    @property
    def used(self):
        return self.ledger.access()['used']

    @property
    def remaining(self):
        state = self.ledger.access()
        return state['limit'] - state['used']

    def check(self, planned):
        if planned > self.remaining:
            raise CallLimitExceeded(f'Planned {planned} new provider calls; {self.remaining} remain in shared pipeline limit. No call made.')

    def consume(self):
        from .call_budget_ledger import LedgerLimitExceeded
        try:
            self.ledger.access(consume=True)
        except LedgerLimitExceeded as exc:
            raise CallLimitExceeded(str(exc)) from exc
