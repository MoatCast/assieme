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


@dataclass
class Session:
    agent: str
    source: str         # adapter name
    calls: int = 0
    tokens: int = 0
    output: int = 0
    peak: int = 0       # largest single-call input: the context size
    turns: list = field(default_factory=list)
    refusals: collections.Counter = field(default_factory=collections.Counter)

    def wake(self, sender, channel, text='', show_text=False, ts=None):
        self.turns.append(Turn(sender, channel, text=' '.join(text.split())[:80] if show_text else '', ts=ts))

    def call(self, input_tokens, output_tokens=0):
        self.calls += 1
        self.tokens += input_tokens
        self.output += output_tokens
        self.peak = max(self.peak, input_tokens)
        if self.turns:
            self.turns[-1].calls += 1
            self.turns[-1].tokens += input_tokens
