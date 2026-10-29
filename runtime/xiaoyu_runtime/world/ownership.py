"""对话负责人（R4.2，见 docs/development/runtime-refactor-design 第 6 节）。

每段对话由一台 Runtime 负责：只有它替这段对话做决定、回话；别的电脑收到这段对话里的话
就转给它。负责人记成绑定记录 {conversation, owner, generation}，作为写它那台的记录（类型
owner）随小鱼网络同步。

谁能写下一代：只有当前负责人。所以每台都能用同一个规则、从同样的记录推出同一个负责人，
不需要另外的协调服务器：

- 第 0 代：主对话没有第 0 代；旁聊的第 0 代是创建它的那台（对话目录的 owner 字段），
  没记的旧旁聊跟主对话同一个负责人。
- 主对话第 1 代：配置了 mesh.home 的那台写给自己。两台都写了（配置错误）就取 Runtime
  编号小的，并报冲突。
- 第 g 代（g ≥ 1，旁聊从 1、主对话从 2 起）：只认第 g−1 代负责人写的那条。

还没有任何绑定时 owner 是 None：调用方照旧由收到的那台回答（R4.2 之前的样子）。
这里只有纯函数，存取和同步在 mesh.py。
"""

from typing import Any, Dict, Iterable, List, Optional

MAIN = "default"


def binding(conversation: str, owner: str, generation: int, author: str) -> Dict[str, Any]:
    return {"conversation": conversation, "owner": owner, "generation": generation, "by": author}


def valid(item: Any) -> bool:
    return (isinstance(item, dict) and isinstance(item.get("conversation"), str)
            and isinstance(item.get("owner"), str) and item["owner"]
            and isinstance(item.get("by"), str) and item["by"]
            and isinstance(item.get("generation"), int) and not isinstance(item.get("generation"), bool)
            and item["generation"] >= 1)


def resolve(conversation: str, records: Iterable[Dict[str, Any]], creator: Optional[str] = None,
            main_owner: Optional[str] = None) -> Dict[str, Any]:
    """从绑定记录推出现在的负责人：{"owner", "generation", "conflict", "by"}。

    records 是网络里所有台写的、这段对话的绑定（每条带 by：写它的那台）。creator 是旁聊
    的创建者（第 0 代）；main_owner 是主对话现在的负责人（给没记创建者的旧旁聊用）。"""
    found = [item for item in records if valid(item) and item["conversation"] == conversation]
    if conversation == MAIN:
        owner, generation = None, 0
    else:
        owner, generation = (creator or main_owner), 0
    conflict = False
    by: Optional[str] = None
    if conversation == MAIN:
        claims = sorted(item["by"] for item in found if item["generation"] == 1 and item["owner"] == item["by"])
        if claims:
            owner, generation, by = claims[0], 1, claims[0]
            conflict = len(set(claims)) > 1
    while owner is not None:
        nxt = [item for item in found if item["generation"] == generation + 1 and item["by"] == owner]
        if not nxt:
            break
        item = nxt[-1]
        owner, generation, by = item["owner"], item["generation"], item["by"]
    return {"owner": owner, "generation": generation, "conflict": conflict, "by": by}


def handed_away(author: str, conversation: str, records: Iterable[Dict[str, Any]],
                current: Optional[str]) -> Optional[int]:
    """author 把这段对话交出去时那条绑定记录的序号（oseq）；它现在又是负责人、或者没交过时 None。

    records 带 oseq（author 自己的序号）。收到 author 在这之后写的这段对话的回复和新事，要拒绝。"""
    if current == author:
        return None
    gave = [item["oseq"] for item in records
            if valid(item) and item["conversation"] == conversation and item["by"] == author
            and item["owner"] != author and isinstance(item.get("oseq"), int)]
    return max(gave) if gave else None


def conversations(records: Iterable[Dict[str, Any]]) -> List[str]:
    return sorted({item["conversation"] for item in records if valid(item)})


def authority(records: Iterable[Dict[str, Any]]) -> Optional[str]:
    """权限权威：写主对话第 1 代绑定的那台（配置了 mesh.home 的）。主对话之后移交给谁都不变。"""
    claims = sorted(item["by"] for item in records if valid(item) and item["conversation"] == MAIN
                    and item["generation"] == 1 and item["owner"] == item["by"])
    return claims[0] if claims else None
