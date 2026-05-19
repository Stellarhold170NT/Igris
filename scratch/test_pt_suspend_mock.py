import asyncio
from prompt_toolkit import PromptSession
from prompt_toolkit.input import DummyInput
from prompt_toolkit.output import DummyOutput
from contextlib import contextmanager

class ActiveDummyInput(DummyInput):
    @property
    def closed(self) -> bool:
        return False
    def attach(self, input_ready_callback):
        @contextmanager
        def dummy_ctx():
            yield
        return dummy_ctx()

# We will record calls to render to verify if suspension works
render_calls = []

async def main():
    # Use ActiveDummyInput and DummyOutput to bypass win32 console check and EOFError
    dummy_input = ActiveDummyInput()
    dummy_output = DummyOutput()
    
    session = PromptSession(input=dummy_input, output=dummy_output)
    app = session.app
    
    original_invalidate = app.invalidate
    original_render = app.renderer.render
    suspend_draw = False

    def new_invalidate():
        if suspend_draw:
            render_calls.append("invalidate_ignored")
            return
        original_invalidate()

    def new_render(app_arg, layout, is_done=False):
        if suspend_draw:
            render_calls.append("render_ignored")
            return
        render_calls.append("render_executed")
        original_render(app_arg, layout, is_done=is_done)

    app.invalidate = new_invalidate
    app.renderer.render = new_render

    # Start the prompt session in the background
    prompt_task = asyncio.create_task(session.prompt_async())
    await asyncio.sleep(0.1) # Let it start and do initial draw
    
    # 1. Trigger initial prompt display (should render)
    print("--- Initial state (drawing enabled) ---")
    app.invalidate()
    await asyncio.sleep(0.1)
    
    # 2. Suspend drawing
    print("\n--- Suspending draw ---")
    suspend_draw = True
    
    # 3. Simulate background prints triggering redraws via app.invalidate
    app.invalidate()
    app.invalidate()
    await asyncio.sleep(0.1)
    
    # 4. Resume drawing
    print("\n--- Resuming draw ---")
    suspend_draw = False
    app.invalidate()
    await asyncio.sleep(0.1)

    # Clean up and exit the app
    app.exit()
    try:
        await prompt_task
    except Exception:
        pass

    print("\n--- Render Calls Log ---")
    for call in render_calls:
        print(f"Call: {call}")

if __name__ == "__main__":
    asyncio.run(main())
