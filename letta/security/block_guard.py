"""Block mutation guards — read_only enforcement at the data access layer.

These guards are the safety net for code paths that mutate blocks at any
layer below the tool executor (which has its own read_only guards, Layer 2).
These guards (Layer 3) catch mutations from:

- REST API endpoints (internal_blocks.py)
- V1 Agent base.py path (agent.py)
- Batch agent (_bulk_rethink_memory_async)
- Agent-scoped core-memory PATCH route (modify_block_by_label_async)
- Bulk value updates (bulk_update_block_values_async)
- Checkpoint undo/redo (undo/redo_checkpoint_block)
- Attach/detach (attach_block_async / detach_block_async)
- Any future code path that calls block_manager directly

The guards prevent:
1. Deleting a read-only block
2. Changing the label of a read-only block (disables canary lookup)
3. Flipping the read_only flag on a read-only block (escalation)
4. Changing the VALUE of a read-only block (v0.16.37 — a canary value
   rewrite is a full defeat even with label/flag guarded)
5. Changing the DESCRIPTION of a read-only block (v0.16.37, review P-6 —
   description is rendered into the system prompt, so it is an injection
   channel into exactly the blocks marked as protected)
6. Relabeling any block TO __canary__, or attaching a second __canary__
   block (v0.16.37, review P-15 — load_canary arms the first __canary__
   block it finds; a writable impostor with a known value could become
   the armed canary)

The read_only flag is one-way at this layer: True -> True is a no-op and
allowed; True -> False is refused. There is no REST escape hatch. If a
framework path ever needs to clear it, it goes through a code change with
an audit trail, not an API field.
"""

from letta.constants import READ_ONLY_BLOCK_EDIT_ERROR

# Fields that may not change on a read-only block (review P-6: description
# reaches the system prompt, so it is NOT cosmetic).
PROTECTED_BLOCK_FIELDS = frozenset({"value", "label", "read_only", "description"})


def _canary_label() -> str:
    from letta.security.canary import CanaryChecker

    return CanaryChecker.CANARY_BLOCK_LABEL


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


def enforce_read_only_mutation(block, changes: dict, teardown: bool = False) -> None:
    """Refuse mutations of protected fields on a read-only block.

    This is the lowest-common-layer guard (v0.16.37, review P-1/P-6): every
    block-mutating function routes its change set through here BEFORE
    applying it, so the five bypass routes that skipped the old
    update_block_async-only guard (modify_block_by_label_async,
    bulk_update_block_values_async, undo/redo, detach) are covered.

    Guards value, label, description (all reach the prompt; a canary value
    rewrite or description injection is a full or partial defeat) and the
    read_only flag (True -> False is escalation; True -> True is a no-op).

    Args:
        block: The current block object (with .read_only attribute).
        changes: Dict of field -> new value being applied.
        teardown: True only for cascade teardown (agent/folder/template/
            source deletion). Teardown deletes the block entirely; guarding
            it would break deletion of agents that own canary blocks.

    Raises:
        ValueError: If the block is read-only and a protected field changes.
    """
    if teardown or not block.read_only:
        return

    for field in PROTECTED_BLOCK_FIELDS:
        if field not in changes:
            continue
        new_value = changes[field]
        if field == "read_only":
            # One-way: True -> True is a no-op; True -> False is escalation.
            if new_value is False:
                raise ValueError(READ_ONLY_BLOCK_EDIT_ERROR)
            continue
        if getattr(block, field, None) != new_value:
            raise ValueError(READ_ONLY_BLOCK_EDIT_ERROR)


def enforce_canary_label_protection(block, changes: dict) -> None:
    """Refuse relabeling a block TO __canary__ (review P-15).

    load_canary arms the FIRST block whose label is __canary__. Relabeling
    an ordinary writable block to __canary__ with a known value plants an
    impostor that can become the armed canary depending on block order.
    The label is reserved: blocks are born with it (agent-creation payloads,
    framework minting) or they never get it.

    Raises:
        ValueError: If the change renames a non-canary block to __canary__.
    """
    canary = _canary_label()
    if changes.get("label") == canary and block.label != canary:
        raise ValueError(
            f"The label '{canary}' is reserved for the framework's exfiltration canary. "
            "Relabeling a block to this label is not permitted."
        )
