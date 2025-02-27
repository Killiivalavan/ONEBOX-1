from typing import Dict, Any, Callable, Awaitable, Optional, List
import asyncio
import logging
from datetime import datetime
from enum import Enum
import uuid

class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

class Task:
    def __init__(self, task_type: str, params: Dict[str, Any]):
        self.id = str(uuid.uuid4())
        self.type = task_type
        self.params = params
        self.status = TaskStatus.PENDING
        self.progress = 0
        self.message = ""
        self.error = None
        self.created_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
        self.result = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "status": self.status.value,
            "progress": self.progress,
            "message": self.message,
            "error": str(self.error) if self.error else None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "result": self.result
        }

    def update(self, progress: int, message: str):
        self.progress = progress
        self.message = message
        self.updated_at = datetime.utcnow()

class TaskManager:
    def __init__(self):
        self._tasks: Dict[str, Task] = {}
        self._logger = logging.getLogger(__name__)
        self._handlers: Dict[str, Callable[[Task], Awaitable[None]]] = {}

    def register_handler(self, task_type: str, handler: Callable[[Task], Awaitable[None]]):
        """Register a handler for a specific task type"""
        self._handlers[task_type] = handler

    async def create_task(self, task_type: str, params: Dict[str, Any]) -> Task:
        """Create and start a new background task"""
        if task_type not in self._handlers:
            raise ValueError(f"No handler registered for task type: {task_type}")

        task = Task(task_type, params)
        self._tasks[task.id] = task
        
        # Start task execution in background
        asyncio.create_task(self._execute_task(task))
        
        return task

    async def _execute_task(self, task: Task):
        """Execute a task and handle its lifecycle"""
        try:
            task.status = TaskStatus.RUNNING
            handler = self._handlers[task.type]
            await handler(task)
            task.status = TaskStatus.COMPLETED
        except Exception as e:
            self._logger.error(f"Task {task.id} failed: {str(e)}", exc_info=True)
            task.status = TaskStatus.FAILED
            task.error = str(e)
        finally:
            task.updated_at = datetime.utcnow()

    def get_task(self, task_id: str) -> Optional[Task]:
        """Get task by ID"""
        return self._tasks.get(task_id)

    def get_tasks_by_type(self, task_type: str) -> List[Task]:
        """Get all tasks of a specific type"""
        return [task for task in self._tasks.values() if task.type == task_type]

    def cleanup_old_tasks(self, max_age_hours: int = 24):
        """Remove completed tasks older than specified hours"""
        now = datetime.utcnow()
        to_remove = []
        for task_id, task in self._tasks.items():
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                age = now - task.updated_at
                if age.total_seconds() > max_age_hours * 3600:
                    to_remove.append(task_id)
        
        for task_id in to_remove:
            del self._tasks[task_id]

# Create global task manager instance
task_manager = TaskManager() 