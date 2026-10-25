"""Explicit generation-local opt-in for automatic narration quality processing."""
from .util import AppError


def mode(config):
    value=config.get('post_processing','off')
    if value not in ('off','on'):
        raise AppError('Post-processing mode must be off or on.')
    return value


def enabled(config):
    return mode(config)=='on'


def disclosure(config):
    selected=mode(config)
    return {'mode':selected,'recognition_status':'enabled'if selected=='on'else'not_checked',
            'acoustic_stress_status':'not_checked',
            'automatic_retakes':selected=='on'}
