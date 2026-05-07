from app.runtime.agent_adapters import AgentInvocation, MockAgentAdapter


class AgentRunner(MockAgentAdapter):
    """Backward-compatible alias for the old mock runner."""

    async def run(self, query, system_prompt, enabled_tools, retrieved_chunks):
        invocation = AgentInvocation(
            app_name="assistant",
            query=query,
            system_prompt=system_prompt,
            model_provider="mock",
            model_name="mock-react",
            enabled_tools=enabled_tools,
            retrieved_chunks=retrieved_chunks,
        )
        async for event in super().run(invocation):
            yield event
