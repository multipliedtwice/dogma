"""Bound native inference by completed outputs, never by CPU activity."""
import time
import subprocess
from contextlib import contextmanager

class CandidateRenderTimeout(TimeoutError):
    pass

class CandidateRenderFailure(RuntimeError):
    """A bounded candidate failed; completed receipt entries remain usable."""
    pass

@contextmanager
def owned_native_process(command,**kwargs):
    process=subprocess.Popen(command,**kwargs)
    try:yield process
    finally:
        if process.poll() is None:
            from .cli_process import terminate_process_tree
            terminate_process_tree(process)

class ProgressBudget:
    def __init__(self,completed=0,seconds=900,clock=time.monotonic):
        self.completed=completed;self.seconds=seconds;self.clock=clock;self.updated=clock()

    def observe(self,completed):
        if completed<self.completed:raise ValueError('Completed native outputs disappeared')
        if completed>self.completed:self.completed=completed;self.updated=self.clock()
        if self.clock()-self.updated>=self.seconds:
            raise CandidateRenderTimeout('Native inference made no completed-output progress within its time budget')
