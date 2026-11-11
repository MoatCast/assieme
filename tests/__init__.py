"""Tests never touch the person's own state: anything a test doesn't isolate itself lands in a throwaway folder."""
import os
import tempfile

os.environ['XDG_STATE_HOME'] = tempfile.mkdtemp(prefix='assieme-tests-')
