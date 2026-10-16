"""Restate service with one journaled file operation and reconciliation."""
import argparse
import asyncio
import restate
import hypercorn.asyncio
import hypercorn.config
from axiocairn import run_file_operation

service = restate.Service('AxiocairnFiles')


@service.handler()
async def recover(ctx: restate.Context, request: dict) -> dict:
    async def perform() -> dict:
        return await asyncio.to_thread(run_file_operation, request)
    return await ctx.run_typed('file-operation-and-reconcile', perform)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=9080)
    settings = hypercorn.config.Config()
    settings.bind = [f'127.0.0.1:{parser.parse_args().port}']
    asyncio.run(hypercorn.asyncio.serve(restate.app(services=[service]), settings))
