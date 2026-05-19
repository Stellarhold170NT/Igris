import asyncio
import sys
import time
import threading
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout

class TaskStatus:
    RUNNING = "running"
    CANCELLED = "cancelled"
    COMPLETED = "completed"

class TaskRecord:
    def __init__(self):
        self.status = TaskStatus.RUNNING
        self._cancel_requested = threading.Event()

    @property
    def cancel_requested(self):
        return self._cancel_requested

    def request_cancel(self):
        self.status = TaskStatus.CANCELLED
        self._cancel_requested.set()

class TaskRegistry:
    def __init__(self):
        self.tasks = []

    def create(self):
        t = TaskRecord()
        self.tasks.append(t)
        return t

    def list_recent(self, n=50):
        return self.tasks[-n:]

class State:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.current_task = None
        self.current_cancel_event = None
        self.loop = None
        self.exit_requested = False
        self.task_registry = TaskRegistry()

    def is_dispatch_running(self):
        return self.current_task is not None and not self.current_task.done()

    def cancel_current_dispatch(self):
        if self.current_cancel_event is not None:
            self.current_cancel_event.set()
        # Cancel running tasks in task registry
        for task_rec in self.task_registry.list_recent():
            if task_rec.status == TaskStatus.RUNNING:
                print("Setting task_rec.cancel_requested!")
                task_rec.request_cancel()
        task = self.current_task
        if task is not None and not task.done():
            if self.loop is not None:
                self.loop.call_soon_threadsafe(task.cancel)
            else:
                task.cancel()

def synchronous_worker(text, cancel_requested, done_callback):
    # Simulate a slow investigation step with live output
    try:
        for i in range(50):
            if cancel_requested.is_set():
                print("Worker thread: Detected task.cancel_requested, exiting.")
                break
            print(f"Agent investigating '{text}' step {i}/50...")
            sys.stdout.flush()
            time.sleep(0.1)
    finally:
        done_callback()

async def main():
    try:
        session = PromptSession()
        is_dummy = False
    except Exception:
        # Fallback to dummy for automated test execution
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

        session = PromptSession(input=ActiveDummyInput(), output=DummyOutput())
        is_dummy = True

    app = session.app
    state = State()
    state.loop = asyncio.get_running_loop()

    suspend_draw = False

    # Setup suspendable draw/render mechanics
    original_invalidate = app.invalidate
    original_render = app.renderer.render

    def should_suspend():
        return state.is_dispatch_running() and suspend_draw

    def new_invalidate():
        if should_suspend():
            try:
                app.renderer.erase()
            except Exception:
                pass
            return
        original_invalidate()

    def new_render(app_arg, layout, is_done=False):
        if should_suspend():
            return
        original_render(app_arg, layout, is_done=is_done)

    app.invalidate = new_invalidate
    app.renderer.render = new_render

    async def _run_one_dispatch(text):
        nonlocal suspend_draw
        dispatch_cancel = threading.Event()
        state.current_cancel_event = dispatch_cancel

        print(f"\nUser typed: {text}")
        suspend_draw = True  # Enable drawing suspension to hide prompt

        thread_done = asyncio.Event()
        worker_exc = None

        task = state.task_registry.create()

        def run_thread():
            nonlocal worker_exc
            try:
                synchronous_worker(
                    text,
                    task.cancel_requested,
                    lambda: state.loop.call_soon_threadsafe(thread_done.set)
                )
            except Exception as exc:
                worker_exc = exc

        worker_thread = threading.Thread(target=run_thread, daemon=True)
        worker_thread.start()

        try:
            await thread_done.wait()
            if worker_exc is not None:
                raise worker_exc
        except (asyncio.CancelledError, KeyboardInterrupt):
            dispatch_cancel.set()
            try:
                await asyncio.shield(thread_done.wait())
            except KeyboardInterrupt:
                pass
            print("· interrupted")
            raise asyncio.CancelledError
        finally:
            suspend_draw = False
            if state.current_cancel_event is dispatch_cancel:
                state.current_cancel_event = None
            app.invalidate()

    async def _processor():
        while not state.exit_requested:
            try:
                text = await state.queue.get()
            except asyncio.CancelledError:
                return
            state.current_task = asyncio.create_task(_run_one_dispatch(text))
            try:
                await state.current_task
            except (asyncio.CancelledError, Exception):
                pass
            state.current_task = None
            state.queue.task_done()

    processor_task = asyncio.create_task(_processor())

    if is_dummy:
        # Automated test simulation
        print("Running in automated dummy mode. Simulating turn...")
        await state.queue.put("test_alert")
        await asyncio.sleep(0.3)  # Let it start
        assert state.is_dispatch_running(), "Dispatch should be running"
        print("Simulating Ctrl+C cancellation...")
        state.cancel_current_dispatch()
        
        # Await current task cancellation
        if state.current_task is not None:
            try:
                await state.current_task
            except asyncio.CancelledError:
                pass
        
        print("Verifying task status...")
        recent_tasks = state.task_registry.list_recent()
        assert len(recent_tasks) == 1
        assert recent_tasks[0].status == TaskStatus.CANCELLED, "Task should be cancelled"
        print("SUCCESS: Automated Ctrl+C simulation test passed.")
        processor_task.cancel()
        return

    ctrl_c_count = 0

    with patch_stdout(raw=True):
        while True:
            try:
                text = await session.prompt_async("❯ ")
                ctrl_c_count = 0  # Reset on successful prompt return
                if text == "exit":
                    state.exit_requested = True
                    break
                await state.queue.put(text)
            except KeyboardInterrupt:
                if state.is_dispatch_running():
                    state.cancel_current_dispatch()
                    if state.current_task is not None:
                        try:
                            await state.current_task
                        except (asyncio.CancelledError, Exception):
                            pass
                    continue
                
                ctrl_c_count += 1
                if ctrl_c_count == 1:
                    print("\n(To exit, press Ctrl+C again or type exit)")
                else:
                    print("\nExiting...")
                    state.exit_requested = True
                    break
            except EOFError:
                break

    processor_task.cancel()
    try:
        await processor_task
    except Exception:
        pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
