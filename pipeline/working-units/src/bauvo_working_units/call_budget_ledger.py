"""Small locked attempt ledger shared by the PDF pipeline's child processes."""
import fcntl
import json
import os
from pathlib import Path

ENVIRONMENT_KEY = 'BAUVO_PROVIDER_CALL_BUDGET_FILE'


class LedgerLimitExceeded(RuntimeError):
    pass


class CallLedger:
    def __init__(self, path):
        self.path = Path(path)

    @classmethod
    def create(cls, path, limit):
        if type(limit) is not int or limit < 0:
            raise ValueError('Call limit must be a nonnegative integer')
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x') as handle:
            json.dump({'limit':limit, 'used':0}, handle)
        return cls(path)

    def access(self, consume=False):
        with self.path.open('r+') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            state = json.load(handle)
            if (set(state) != {'limit','used'} or any(type(v) is not int or v<0 for v in state.values())
                    or state['used'] > state['limit']):
                raise ValueError('Invalid provider call ledger')
            if consume:
                if state['used'] >= state['limit']:
                    raise LedgerLimitExceeded(f"Provider call limit reached: {state['used']}/{state['limit']}; no new request sent")
                state['used'] += 1
                handle.seek(0);json.dump(state,handle);handle.truncate();handle.flush();os.fsync(handle.fileno())
            return state


def consume_from_environment(phase):
    """Opted-in pipeline children use its one shared limit, including every retry."""
    path = os.environ.get(ENVIRONMENT_KEY)
    if path:
        ledger = CallLedger(path)
        state = ledger.access()
        print(f"Request plan: {phase}; 1 new attempt; remaining shared limit={state['limit']-state['used']}", flush=True)
        ledger.access(consume=True)
