"""Приводит в порядок то, что записано раньше.

Волну у голосовых и обложку у кружочков сервер считает при приёме — но
записанное до этого лежит без них, а кружочки вдобавок лежат такими, какими
их отдал телефон: лёжа и с неквадратным пикселем. Этот проход догоняет их.

    python tidy-media.py --посмотреть   — что бы сделал, ничего не трогая
    python tidy-media.py                — сделать

Сервер при этом можно не останавливать: старые файлы не переписываются на
месте, а заменяются новыми, и запись в базе меняется последней. Но копию
всё-таки снимите: ~/velix/backup.sh — это полминуты.
"""

import asyncio
import sys
from pathlib import Path

import mediatools
import storage

ПОСМОТРЕТЬ = "--посмотреть" in sys.argv or "--dry-run" in sys.argv


def файл_вложения(media_id):
    найдено = list(storage._media_dir.glob(f"{media_id}*"))
    return найдено[0] if найдено else None


def сохранить(данные, suffix):
    """Кладёт новый файл рядом и возвращает его имя."""
    import uuid
    новый = uuid.uuid4().hex
    (storage._media_dir / f"{новый}{suffix}").write_bytes(данные)
    return новый


async def главное():
    if not mediatools.available():
        print("ffmpeg рядом не нашёлся — делать нечего")
        return 1

    storage._init_sync(storage.DB_PATH, storage.MEDIA_DIR)
    with storage._lock:
        строки = storage._connection.execute(
            "SELECT id, kind, media_id, media_name, waveform, poster"
            " FROM messages WHERE kind IN ('voice', 'circle') AND deleted = 0"
            " ORDER BY id").fetchall()

    print(f"записей: {len(строки)}"
          + (" (только смотрим)" if ПОСМОТРЕТЬ else ""))
    тронуто = 0

    for номер, вид, media_id, имя, волна, обложка in строки:
        файл = файл_вложения(media_id) if media_id else None
        if файл is None:
            print(f"  {номер}: файла нет, пропускаем")
            continue

        хвост = файл.suffix or (".m4a" if вид == "voice" else ".mp4")
        данные = файл.read_bytes()
        было = len(данные)

        новая_волна = волна
        новая_обложка = обложка
        новый_id = None

        if вид == "voice" and not волна:
            новая_волна = await mediatools.waveform(данные, хвост) or None
            print(f"  {номер}: голос {было} байт — волна"
                  + (" посчитана" if новая_волна else " не вышла"))

        if вид == "circle":
            ровный = await mediatools.tidy_circle(данные, хвост)
            if ровный and not ПОСМОТРЕТЬ:
                новый_id = сохранить(ровный, ".mp4")
                данные = ровный
            elif ровный:
                данные = ровный
            print(f"  {номер}: кружочек {было} → {len(данные)} байт"
                  + ("" if ровный else " (пересобрать не вышло)"))

            if not обложка:
                кадр = await mediatools.circle_poster(данные, ".mp4")
                if кадр and not ПОСМОТРЕТЬ:
                    новая_обложка = сохранить(кадр, ".jpg")
                print(f"       обложка: {'снята' if кадр else 'не вышла'}")

        if ПОСМОТРЕТЬ:
            continue

        if новая_волна == волна and новая_обложка == обложка and новый_id is None:
            continue

        with storage._lock:
            storage._connection.execute(
                "UPDATE messages SET waveform = ?, poster = ?, media_id = ?,"
                " media_size = ?, media_name = ? WHERE id = ?",
                (новая_волна, новая_обложка, новый_id or media_id,
                 len(данные), Path(имя or "circle.mp4").with_suffix(
                     ".mp4").name if новый_id else имя, номер))
            storage._connection.commit()

        # Прежний файл убираем только теперь, когда запись уже смотрит на новый
        if новый_id:
            try:
                файл.unlink()
            except OSError:
                pass
        тронуто += 1

    storage._close_sync()
    print(f"поправлено записей: {тронуто}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(главное()))
