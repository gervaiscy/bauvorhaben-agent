"""Provider switch outside domain assessment logic; cache and call limits are shared."""
import copy
import os
from pathlib import Path
from ...cache import CachedModel
from ...call_budget import BudgetedModel


def provider_identity(provider='gemini', project=None, location='global', model=None, reasoning='medium'):
    if provider == 'sol':
        return {'provider': 'sol', 'model': model or 'gpt-6.1-sol', 'reasoning': reasoning}
    if provider != 'gemini':
        raise ValueError('Unknown consequence provider')
    return {'project': project, 'location': location, 'model': model or 'gemini-3.5-flash'}


def backend(output, cache_dir, budget, *, provider='gemini', project=None, location='global',
            model=None, reasoning='medium', max_cost_usd='30', spend_ledger=None,
            factory=None, plan_only=False):
    identity = provider_identity(provider, project, location, model, reasoning)
    output = Path(output)
    def create():
        from ...spend_budget import SpendLedger, SpendLimitedModel
        if provider == 'sol':
            from ...sol import SolModel
            if factory is None and not os.environ.get('OPENAI_API_KEY'):
                raise ValueError('Set OPENAI_API_KEY for new Sol requests')
            provider_factory = factory or (lambda: SolModel(identity['model'], reasoning))
        else:
            from ...gemini import GeminiModel
            if factory is None and not project:
                raise ValueError('Set --project for new Gemini requests')
            provider_factory = factory or (lambda: GeminiModel(project, location, identity['model']))
        ledger = SpendLedger(spend_ledger or output.parent/'spend.json', max_cost_usd,
                             identity['model'], 'openai' if provider == 'sol' else location)
        return SpendLimitedModel(provider_factory, ledger)
    return CachedModel(BudgetedModel(create, budget), cache_dir or output.parent/'provider-responses',
        identity, budget=budget, plan_path=output.with_name(output.stem+'.request-plan.json'), plan_only=plan_only)


class PhaseRouter:
    """Route only consequences to the selected backend, preserving diagnostics."""
    def __init__(self, base, consequences):
        self.base, self.consequences = base, consequences
        self.last_raw_response = self.last_finish_reason = self.last_diagnostics = None

    def prepare(self, prompts):
        from ...call_budget import prepare_requests
        for target, wanted in ((self.base, False), (self.consequences, True)):
            selected = [p for p in prompts if p.startswith('PAGE_CONSEQUENCES.') == wanted]
            if selected:
                prepare_requests(target, selected)

    def extract(self, prompt):
        target = self.consequences if prompt.startswith('PAGE_CONSEQUENCES.') else self.base
        self.last_raw_response = self.last_finish_reason = self.last_diagnostics = None
        try:
            return target.extract(prompt)
        finally:
            self.last_raw_response = getattr(target, 'last_raw_response', None)
            self.last_finish_reason = getattr(target, 'last_finish_reason', None)
            self.last_diagnostics = copy.deepcopy(getattr(target, 'last_diagnostics', None))
