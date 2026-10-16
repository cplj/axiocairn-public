"""Inngest worker served over loopback, with explicit local API URLs."""
import argparse
import asyncio
import inngest
import inngest.fast_api
from fastapi import FastAPI
import uvicorn
from axiocairn import run_file_operation


def app(server):
    client = inngest.Inngest(app_id='axiocairn-files', is_production=False,
                             event_api_base_url=server, api_base_url=server)

    @client.create_function(fn_id='file-recovery', trigger=inngest.TriggerEvent(event='axiocairn/file'), retries=0)
    async def recover(ctx: inngest.Context):
        async def perform():
            return await asyncio.to_thread(run_file_operation, ctx.event.data)
        return await ctx.step.run('file-operation-and-reconcile', perform)

    api = FastAPI()
    inngest.fast_api.serve(api, client, [recover])
    return api


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', default='http://127.0.0.1:8288')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(app(args.server), host='127.0.0.1', port=args.port)
