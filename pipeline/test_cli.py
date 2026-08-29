import asyncio
import sys
from google.antigravity import Agent, LocalAgentConfig, CapabilitiesConfig
from google.antigravity.utils.interactive import run_interactive_loop

async def main():
    print("Welcome to the Antigravity Interactive CLI (Python SDK).")
    print("Type your message below. Press Ctrl+D to exit.\n")
    
    # Configure the agent with capabilities (like file reading/writing if needed)
    config = LocalAgentConfig(capabilities=CapabilitiesConfig())
    
    try:
        async with Agent(config) as agent:
            await run_interactive_loop(agent)
    except Exception as e:
        print(f"\nFailed to start the interactive loop: {e}")
        print("Please check that your IDE's authentication is working, or that you have passed valid API keys.")

if __name__ == "__main__":
    asyncio.run(main())
