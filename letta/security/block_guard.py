"""Block mutation guards — read_only enforcement at the data access layer.

These guards are the safety net for code paths that call block_manager
directly (bypassing the tool executor's pre-execution checks). The tool
executor has its own read_only guards (Layer 2) that provide better error
messages. These guards (Layer 3) catch mutations from:

- REST API endpoints (internal_blocks.py)
- V1 Agent base.py path (agent.py)
- Batch agent (_bulk_rethink_memory_async)
- Any future code path that calls block_manager directly

The guards prevent:
1. Deleting a read-only block
2. Changing the label of a read-only block (disables canary lookup)
3. Flipping the read_only flag on a read-only block (escalation)

Description changes on read-only blocks are allowed — they're cosmetic,
not a security boundary. The tool executor guards them anyway for
consistency, but the data model guard doesn't need to.
"""

from typing import Optional

from letta.constants import READ_ONLY_BLOCK_EDIT_ERROR


def check_read_only_delete(block_id: str, actor_id: Optional[str] = None) -> None:
    """Pre-deletion hook: check if a block is read-only before allowing deletion.

    This is designed to be called before block_manager.delete_block_async().
    The caller must fetch the block first to check its read_only flag.

    Usage:
        memory_block = agent_state.memory.get_block(label)
        check_read_only_block(memory_block)  # raises if read_only
        await block_manager.delete_block_async(block_id=memory_block.id, actor=actor)

    Args:
        block_id: The block ID to delete (for error messages).
        actor_id: The actor attempting the deletion (for audit logging, future).

    Raises:
        ValueError: If the block is read-only.
    """
    # This is a placeholder for the actual guard pattern.
    # Real usage: check block.read_only before calling block_manager.
    pass


def check_read_only_block(block, operation: str = "modify") -> None:
    """Check if a block is read-only and reject the operation if so.

    This is the core guard function. It takes a block object (any object
    with a .read_only attribute) and raises ValueError if the block is
    read-only.

    Args:
        block: A block object with a .read_only attribute.
        operation: The operation being attempted (for error messages).
            One of: "modify", "delete", "rename", "flag_change".

    Raises:
        ValueError: If the block is read-only.
    """
    if block.read_only:
        raise ValueError(READ_ONLY_BLOCK_EDIT_ERROR)


def check_read_only_update(block, block_update) -> None:
    """Check if a block update tries to change the label or read_only flag
    on a read-only block.

    This guard is designed to be called before block_manager.update_block_async().
    It checks two security-critical fields:
    - label: Changing a read-only block's label disables canary lookup.
    - read_only: Flipping the flag to False is an escalation attack.

    Description changes are allowed — they're cosmetic.

    Args:
        block: The current block object (with .read_only attribute).
        block_update: The BlockUpdate object being applied.

    Raises:
        ValueError: If the block is read-only and the update changes
            the label or read_only flag.
    """
    if not block.read_only:
        return  # Not read-only, any update is fine

    update_data = block_update.model_dump(exclude_unset=True, exclude_none=True)

    # Reject label changes on read-only blocks (security boundary)
    if "label" in update_data and update_data["label"] != block.label:
        raise ValueError(READ_ONLY_BLOCK_EDIT_ERROR)

    # Reject read_only flag flip on read-only blocks (escalation attack)
    if "read_only" in update_data and not update_data["read_only"]:
        raise ValueError(READ_ONLY_BLOCK_EDIT_ERROR)
