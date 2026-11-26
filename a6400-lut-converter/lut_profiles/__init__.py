from .a6400 import a6400_profile

PROFILES = {"a6400": a6400_profile}


def get_profile(name):
    return PROFILES[name]
