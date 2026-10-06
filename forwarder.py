import os
import json
import time
import shutil
import traceback
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    FloodWaitError,
    RPCError,
)

from image_contact import replace_contacts


# =========================================================
# 基础配置
# =========================================================

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STRING = os.environ["SESSION_STRING"]

SOURCE_TO_TARGET_RAW = os.getenv(
    "SOURCE_TO_TARGET",
    "{}"
)

MESSAGE_LIMIT = int(
    os.getenv("MESSAGE_LIMIT", "30")
)

DELAY_SECONDS = float(
    os.getenv("DELAY_SECONDS", "1")
)

MODE = os.getenv(
    "MODE",
    "forward"
).lower()

KEYWORDS_RAW = os.getenv(
    "KEYWORDS",
    ""
)

BLOCK_KEYWORDS_RAW = os.getenv(
    "BLOCK_KEYWORDS",
    ""
)

PREFIX = os.getenv(
    "PREFIX",
    ""
)

SUFFIX = os.getenv(
    "SUFFIX",
    ""
)

REPLACE_IMAGE_CONTACT = (
    os.getenv(
        "REPLACE_IMAGE_CONTACT",
        "false"
    ).lower()
    == "true"
)

PROCESS_OLD = (
    os.getenv(
        "PROCESS_OLD",
        "false"
    ).lower()
    == "true"
)

STATE_DIR = Path(".state")
STATE_FILE = STATE_DIR / "processed.json"

IMAGE_DIR = Path("/tmp/tg_images")

STATE_DIR.mkdir(
    parents=True,
    exist_ok=True
)

IMAGE_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# =========================================================
# 工具函数
# =========================================================

def parse_json(raw):
    try:
        return json.loads(raw)
    except Exception as e:
        raise RuntimeError(
            f"SOURCE_TO_TARGET JSON 格式错误: {e}"
        )


def parse_list(raw):
    return [
        x.strip().lower()
        for x in raw.split(",")
        if x.strip()
    ]


SOURCE_TO_TARGET = parse_json(
    SOURCE_TO_TARGET_RAW
)

KEYWORDS = parse_list(
    KEYWORDS_RAW
)

BLOCK_KEYWORDS = parse_list(
    BLOCK_KEYWORDS_RAW
)


# =========================================================
# 状态
# =========================================================

def load_state():
    if not STATE_FILE.exists():
        return {
            "sources": {}
        }

    try:
        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            data = json.load(f)

        if "sources" not in data:
            data["sources"] = {}

        return data

    except Exception:
        return {
            "sources": {}
        }


def save_state(state):
    temp_file = STATE_FILE.with_suffix(
        ".tmp"
    )

    with open(
        temp_file,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2
        )

    temp_file.replace(
        STATE_FILE
    )


# =========================================================
# 文本过滤
# =========================================================

def message_text(message):
    text = ""

    try:
        text = message.raw_text or ""
    except Exception:
        pass

    return text.strip()


def check_filter(message):
    text = message_text(
        message
    ).lower()

    # 黑名单优先
    if BLOCK_KEYWORDS:
        for keyword in BLOCK_KEYWORDS:
            if keyword in text:
                print(
                    f"[FILTER] block keyword: {keyword}"
                )
                return False

    # 如果配置了关键词，则必须命中
    if KEYWORDS:
        matched = False

        for keyword in KEYWORDS:
            if keyword in text:
                matched = True
                break

        if not matched:
            print(
                "[FILTER] keyword not matched"
            )
            return False

    return True


# =========================================================
# Caption 处理
# =========================================================

def build_caption(message):
    original = message_text(
        message
    )

    return (
        PREFIX
        + original
        + SUFFIX
    ).strip()


# =========================================================
# 图片判断
# =========================================================

def is_photo(message):
    return bool(
        getattr(
            message,
            "photo",
            None
        )
    )


def is_image_document(message):
    document = getattr(
        message,
        "document",
        None
    )

    if not document:
        return False

    mime = getattr(
        document,
        "mime_type",
        ""
    ) or ""

    return mime.startswith(
        "image/"
    )


# =========================================================
# 处理图片
# =========================================================

async def process_image(
    client,
    message,
    target
):
    file_name = (
        f"{message.id}_original"
    )

    input_path = (
        IMAGE_DIR / file_name
    )

    output_path = (
        IMAGE_DIR /
        f"{message.id}_modified.jpg"
    )

    print(
        f"[IMAGE] download message {message.id}"
    )

    downloaded = await client.download_media(
        message,
        file=str(input_path)
    )

    if not downloaded:
        print(
            "[IMAGE] download failed"
        )
        return False

    try:
        modified = replace_contacts(
            input_path,
            output_path
        )

        caption = build_caption(
            message
        )

        if modified and output_path.exists():
            print(
                "[IMAGE] contact information replaced"
            )

            await client.send_file(
                target,
                str(output_path),
                caption=caption or None
            )

        else:
            print(
                "[IMAGE] no contact replacement, send original"
            )

            if MODE == "copy":
                await client.send_message(
                    target,
                    message
                )
            else:
                await client.forward_messages(
                    target,
                    message
                )

        return True

    finally:
        try:
            input_path.unlink(
                missing_ok=True
            )

            output_path.unlink(
                missing_ok=True
            )
        except Exception:
            pass


# =========================================================
# 发送普通消息
# =========================================================

async def send_message(
    client,
    message,
    target
):
    caption = build_caption(
        message
    )

    # 如果需要前后缀，或者选择 copy
    if MODE == "copy" or PREFIX or SUFFIX:
        try:
            # 纯文字
            if not message.media:
                await client.send_message(
                    target,
                    caption
                )

            else:
                # 媒体复制
                await client.send_file(
                    target,
                    message.media,
                    caption=caption or None
                )

            return True

        except Exception as e:
            print(
                f"[SEND] copy failed: {e}"
            )
            raise

    # 默认 forward
    await client.forward_messages(
        target,
        message
    )

    return True


# =========================================================
# 单条消息处理
# =========================================================

async def process_one(
    client,
    message,
    target
):
    print(
        f"[PROCESS] message={message.id}"
    )

    # 过滤
    if not check_filter(
        message
    ):
        return True

    # 图片
    if (
        REPLACE_IMAGE_CONTACT
        and (
            is_photo(message)
            or is_image_document(message)
        )
    ):
        return await process_image(
            client,
            message,
            target
        )

    # 普通消息
    return await send_message(
        client,
        message,
        target
    )


# =========================================================
# 处理频道
# =========================================================

async def process_source(
    client,
    source,
    target,
    state
):
    source_key = str(
        source
    )

    last_id = int(
        state["sources"].get(
            source_key,
            0
        )
    )

    print("")
    print("=" * 70)
    print(
        f"[SOURCE] {source}"
    )
    print(
        f"[TARGET] {target}"
    )
    print(
        f"[STATE] last_id={last_id}"
    )
    print("=" * 70)

    messages = []

    async for message in client.iter_messages(
        source,
        limit=MESSAGE_LIMIT
    ):
        messages.append(
            message
        )

    if not messages:
        print(
            "[SOURCE] no messages"
        )
        return

    # 第一次运行
    if last_id == 0 and not PROCESS_OLD:
        latest_id = max(
            message.id
            for message in messages
            if message.id
        )

        state["sources"][
            source_key
        ] = latest_id

        save_state(
            state
        )

        print(
            f"[INIT] skip old messages, set last_id={latest_id}"
        )

        return

    # 只处理新消息
    new_messages = [
        message
        for message in messages
        if message.id > last_id
    ]

    if not new_messages:
        print(
            "[SOURCE] no new messages"
        )
        return

    # 最老 → 最新
    new_messages.sort(
        key=lambda x: x.id
    )

    print(
        f"[SOURCE] new messages: {len(new_messages)}"
    )

    for message in new_messages:

        try:
            success = await process_one(
                client,
                message,
                target
            )

            if success:
                state["sources"][
                    source_key
                ] = message.id

                save_state(
                    state
                )

                print(
                    f"[OK] processed {source} #{message.id}"
                )

            time.sleep(
                DELAY_SECONDS
            )

        except FloodWaitError as e:
            print(
                f"[FLOOD] Telegram requires waiting {e.seconds}s"
            )

            # 不更新 last_id
            # 下次 Actions 继续处理
            raise

        except RPCError as e:
            print(
                f"[TELEGRAM ERROR] {e}"
            )

            # 当前消息失败，不继续后面的
            # 防止跳过消息
            raise

        except Exception as e:
            print(
                f"[ERROR] message {message.id}: {e}"
            )

            traceback.print_exc()

            # 当前消息失败，不推进状态
            raise


# =========================================================
# 主程序
# =========================================================

async def main():
    print("")
    print("=" * 70)
    print("Telegram Auto Forwarder")
    print("=" * 70)

    if not SOURCE_TO_TARGET:
        raise RuntimeError(
            "SOURCE_TO_TARGET 没有配置"
        )

    print(
        f"[CONFIG] MODE={MODE}"
    )

    print(
        f"[CONFIG] MESSAGE_LIMIT={MESSAGE_LIMIT}"
    )

    print(
        f"[CONFIG] OCR={REPLACE_IMAGE_CONTACT}"
    )

    print(
        f"[CONFIG] SOURCES={len(SOURCE_TO_TARGET)}"
    )

    client = TelegramClient(
        StringSession(
            SESSION_STRING
        ),
        API_ID,
        API_HASH
    )

    await client.start()

    me = await client.get_me()

    print(
        f"[LOGIN] Telegram account: "
        f"{getattr(me, 'username', None) or me.id}"
    )

    state = load_state()

    for source, target in SOURCE_TO_TARGET.items():

        try:
            await process_source(
                client,
                source,
                target,
                state
            )

        except Exception as e:
            print(
                f"[SOURCE ERROR] "
                f"{source}: {e}"
            )

            # 一个频道失败，不影响其他频道
            traceback.print_exc()

    await client.disconnect()

    print("")
    print("=" * 70)
    print("Run completed")
    print("=" * 70)


if __name__ == "__main__":
    import asyncio

    asyncio.run(
        main()
    )
