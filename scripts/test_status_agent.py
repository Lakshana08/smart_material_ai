"""Manual smoke test for the Status Agent over the real A2A/JSON-RPC wire
protocol (not just calling core_capabilities directly in-process).

Usage (server must already be running - see README's "Run locally" section):
    python scripts/test_status_agent.py                     # structured, no LLM
    python scripts/test_status_agent.py --free-text "..."    # free text, uses AI Core
"""

import argparse
import asyncio

import httpx

from a2a.client import ClientFactory
from a2a.client.client import ClientConfig
from a2a.helpers import get_data_parts, get_text_parts, new_data_message, new_text_message
from a2a.types import a2a_pb2

AGENT_URL = "http://127.0.0.1:8000/a2a/status"


async def send(message) -> None:
    async with httpx.AsyncClient(timeout=30) as http_client:
        factory = ClientFactory(ClientConfig(httpx_client=http_client))
        client = await factory.create_from_url(AGENT_URL)
        request = a2a_pb2.SendMessageRequest(message=message)

        async for stream_response in client.send_message(request):
            if stream_response.HasField("message"):
                m = stream_response.message
                print("TEXT:", get_text_parts(m.parts))
                print("DATA:", get_data_parts(m.parts))
            elif stream_response.HasField("task"):
                print("TASK:", stream_response.task)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--free-text", help="Send a plain-text question instead of a structured check.")
    parser.add_argument("--check-type", default="over_control",
                         choices=["non_controlled", "over_control", "aging", "machine_head_review"])
    parser.add_argument("--material")
    parser.add_argument("--storage-location")
    parser.add_argument("--work-order")
    args = parser.parse_args()

    if args.free_text:
        message = new_text_message(args.free_text)
    else:
        payload = {"check_type": args.check_type}
        if args.material:
            payload["material"] = args.material
        if args.storage_location:
            payload["storage_location"] = args.storage_location
        if args.work_order:
            payload["work_order"] = args.work_order
        message = new_data_message(payload)

    asyncio.run(send(message))


if __name__ == "__main__":
    main()
