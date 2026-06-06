from __future__ import annotations


def pop_skip_stack_for_tag(
    skip_stack: list[str],
    *,
    tag: str,
    drop_with_content: frozenset[str],
) -> bool:
    if tag not in drop_with_content or not skip_stack:
        return False
    # Pop up to and including the matching tag to handle mismatched
    # nested tags (e.g. <script><style></script> leaves style orphaned).
    while skip_stack:
        popped = skip_stack.pop()
        if popped == tag:
            break
    return True


def close_matching_open_tag(open_tags: list[str], out: list[str], tag: str) -> bool:
    if not open_tags:
        return False
    if open_tags[-1] == tag:
        open_tags.pop()
        out.append(f"</{tag}>")
        return True
    # Browser-style error recovery: search backwards for a matching open tag.
    for i in range(len(open_tags) - 1, -1, -1):
        if open_tags[i] == tag:
            # Close all intermediate unclosed tags, then the matching one.
            for j in range(len(open_tags) - 1, i - 1, -1):
                out.append(f"</{open_tags[j]}>")
            del open_tags[i:]
            return True
    return False
