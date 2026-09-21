"""Cross-platform subprocess flags for invisible background execution."""
import os
import subprocess


def background_creation_flags(new_process_group=False, detached=False, platform=None):
    if (platform or os.name) != "nt":
        return 0
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if new_process_group:
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    if detached:
        flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
    return flags


def hidden_run_kwargs():
    flags = background_creation_flags()
    return {"creationflags": flags} if flags else {}
