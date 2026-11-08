"""The data model every adapter produces: sessions made of turns, each turn opened by a wake."""
import collections
import datetime as dt
from dataclasses import dataclass, field

HUMAN = 'human'


def parse_ts(ts):
    """ISO-8601 string -> aware datetime, or None. Python 3.10's fromisoformat does not take 'Z'."""
    if not isinstance(ts, str):
        return None
    try:
        t = dt.datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


@dataclass
class Turn:
    """Everything an agent did after one wake, until the next one."""
    sender: str         # agent that sent the wake, or HUMAN
    channel: str        # how it arrived: human, hc, team, subagent, notify, spawn, generic
    calls: int = 0      # model calls the receiver made in this turn
    tokens: int = 0     # input tokens summed over those calls: receiver context x calls
    text: str = ''      # first 80 chars of the wake; filled only with --show-text
    ts: dt.datetime = None  # when the wake arrived
    changed: bool = False   # F5: it edited, delegated or ran something that isn't read-only
    branch: str = None      # F7: the git branch at the time, the unit of work


@dataclass
class Job:
    """F6: one call that ran heavy commands."""
    id: str             # the tool call: a resumed log repeats it
    start: dt.datetime
    end: dt.datetime    # None until a result was seen
    runs: list          # (kind, full suite?, label)
    branch: str = None


@dataclass
class Session:
    agent: str
    source: str         # adapter name
    calls: int = 0
    tokens: int = 0
    output: int = 0
    peak: int = 0       # largest single-call input: the context size
    turns: list = field(default_factory=list)
    refusals: collections.Counter = field(default_factory=collections.Counter)  # (label, why) -> count
    rule_notes: dict = field(default_factory=dict)  # why -> advice (F4)
    cwd: str = None     # its working folder, to pick out one team's sessions
    samples: list = field(default_factory=list)  # F5: (time, input tokens) per call
    jobs: list = field(default_factory=list)     # F6: Job
    failures: list = field(default_factory=list)  # F7: (time, fingerprint, turn number, label)
    cycles: collections.Counter = field(default_factory=collections.Counter)  # F7: branch -> fix or ruling cycles
    refusal_seconds: collections.Counter = field(default_factory=collections.Counter)  # F8: why -> time lost

    def wake(self, sender, channel, text='', show_text=False, ts=None, branch=None):
        self.turns.append(Turn(sender, channel, text=' '.join(text.split())[:80] if show_text else '', ts=ts, branch=branch))

    def call(self, input_tokens, output_tokens=0, ts=None):
        if ts:
            self.samples.append((ts, input_tokens))
        self.calls += 1
        self.tokens += input_tokens
        self.output += output_tokens
        self.peak = max(self.peak, input_tokens)
        if self.turns:
            self.turns[-1].calls += 1
            self.turns[-1].tokens += input_tokens
