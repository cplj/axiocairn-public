"""Temporal worker: file IO occurs in an activity, never in workflow code."""
import argparse
import asyncio
from datetime import timedelta
from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.worker import Worker
with workflow.unsafe.imports_passed_through():
    from axiocairn import run_file_operation


@activity.defn
async def file_operation(request: dict) -> dict:
    return await asyncio.to_thread(run_file_operation, request)


@workflow.defn
class FileRecovery:
    @workflow.run
    async def run(self, request: dict) -> dict:
        return await workflow.execute_activity('file_operation', request,
                     start_to_close_timeout=timedelta(seconds=30),
                     retry_policy=RetryPolicy(maximum_attempts=1))


async def main(address):
    client = await Client.connect(address)
    async with Worker(client, task_queue='axiocairn-files', workflows=[FileRecovery], activities=[file_operation]):
        await asyncio.Event().wait()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--address', default='127.0.0.1:7233')
    asyncio.run(main(parser.parse_args().address))
