# Copyright (C) 2026 jpkrajewski

from ofmhelpers.cache import JobQueue, QueueName, get_redis
lazy from ofmhelpers.features.jobs.runner import Requeued


def make_result(name):
    return [{"name": name, "path": None}]


def always_fails():
    msg = "Wrong API Key"
    raise ValueError(msg)


def fails_with(argument, secret):
    msg = "the job failed"
    raise RuntimeError(msg)


def requeue_once(note):
    return Requeued()


def note_down(name):
    get_redis().rpush("ran", name)


def note_the_line():
    get_redis().set(
        "line_seen",
        JobQueue(name=QueueName.MEDIA).line().model_dump_json(),
    )
