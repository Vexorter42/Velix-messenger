"""Сборка Velix.apk без Gradle.

Gradle тянет за собой десятки мегабайт зависимостей и своё представление о
версиях. Приложению из одного экрана это не нужно: всё, что требуется, уже
лежит в Android SDK — aapt2 собирает ресурсы, javac компилирует, d8 делает
dex, apksigner подписывает.

Запуск:  python android/build.py
Результат:  android/Velix.apk

Ключ для подписи создаётся при первой сборке и лежит рядом (в git не
попадает). Приложение самоподписанное: Android спросит разрешение поставить
его вручную — так и должно быть, магазина тут нет.
"""

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
KEYSTORE = HERE / "velix.keystore"
KEY_ALIAS = "velix"
KEY_PASSWORD = "velixvelix"      # ключ для самоподписи, секрета в нём нет

MIN_SDK = "24"
TARGET_SDK = "34"


def sdk_root():
    for candidate in (os.environ.get("ANDROID_HOME"),
                      os.environ.get("ANDROID_SDK_ROOT"),
                      Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk",
                      Path.home() / "Android" / "Sdk"):
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    raise SystemExit("не нашёл Android SDK: задайте ANDROID_HOME")


def newest(folder):
    """Самая свежая версия из каталога вроде build-tools или platforms."""
    versions = sorted((path for path in folder.iterdir() if path.is_dir()),
                      key=lambda path: [int(part) if part.isdigit() else 0
                                        for part in path.name.replace(
                                            "android-", "").split(".")])
    if not versions:
        raise SystemExit(f"пусто: {folder}")
    return versions[-1]


def java_home():
    """JDK: сначала свой, потом тот, что идёт с Android Studio."""
    if os.environ.get("JAVA_HOME"):
        return Path(os.environ["JAVA_HOME"])
    for candidate in (Path(r"C:\Program Files\Android\Android Studio\jbr"),
                      Path("/usr/lib/jvm/default")):
        if (candidate / "bin").is_dir():
            return candidate
    raise SystemExit("не нашёл JDK: задайте JAVA_HOME")


def run(command, **kwargs):
    # d8 и apksigner — это .bat, которые зовут java из PATH. В системе может
    # стоять древняя восьмёрка, поэтому подсовываем им нужный JDK явно.
    environment = dict(os.environ)
    jdk = java_home()
    environment["JAVA_HOME"] = str(jdk)
    environment["PATH"] = str(jdk / "bin") + os.pathsep + environment.get("PATH", "")

    result = subprocess.run([str(part) for part in command],
                            capture_output=True, text=True, env=environment,
                            encoding="utf-8", errors="replace", **kwargs)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(f"не сработало: {command[0]}")
    return result


def main():
    sdk = sdk_root()
    tools = newest(sdk / "build-tools")
    platform = newest(sdk / "platforms")
    android_jar = platform / "android.jar"
    jdk = java_home()

    executable = ".exe" if sys.platform == "win32" else ""
    batch = ".bat" if sys.platform == "win32" else ""
    aapt2 = tools / f"aapt2{executable}"
    zipalign = tools / f"zipalign{executable}"
    d8 = tools / f"d8{batch}"
    apksigner = tools / f"apksigner{batch}"
    javac = jdk / "bin" / f"javac{executable}"
    keytool = jdk / "bin" / f"keytool{executable}"

    print(f"SDK: {sdk}")
    print(f"инструменты: {tools.name}, платформа: {platform.name}, JDK: {jdk}")

    if BUILD.exists():
        shutil.rmtree(BUILD)
    (BUILD / "classes").mkdir(parents=True)
    (BUILD / "dex").mkdir()
    (BUILD / "gen").mkdir()

    # --- ресурсы
    run([aapt2, "compile", "--dir", HERE / "res", "-o", BUILD / "res.zip"])
    run([aapt2, "link",
         "-o", BUILD / "base.apk",
         "-I", android_jar,
         "--manifest", HERE / "AndroidManifest.xml",
         "-R", BUILD / "res.zip",
         "--java", BUILD / "gen",
         "--min-sdk-version", MIN_SDK,
         "--target-sdk-version", TARGET_SDK,
         "--auto-add-overlay"])

    # --- java -> class -> dex
    sources = list((HERE / "java").rglob("*.java")) + list(BUILD.rglob("R.java"))
    run([javac, "-source", "8", "-target", "8", "-nowarn",
         "-bootclasspath", android_jar, "-classpath", android_jar,
         "-d", BUILD / "classes", *sources])

    classes = list((BUILD / "classes").rglob("*.class"))
    run([d8, "--min-api", MIN_SDK, "--lib", android_jar,
         "--output", BUILD / "dex", *classes])

    # --- dex внутрь apk
    unsigned = BUILD / "unsigned.apk"
    shutil.copy(BUILD / "base.apk", unsigned)
    with zipfile.ZipFile(unsigned, "a", zipfile.ZIP_DEFLATED) as archive:
        archive.write(BUILD / "dex" / "classes.dex", "classes.dex")

    # --- ключ для подписи
    if not KEYSTORE.exists():
        print("создаю ключ для подписи (он останется рядом и в git не попадёт)")
        run([keytool, "-genkeypair", "-keystore", KEYSTORE, "-alias", KEY_ALIAS,
             "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
             "-storepass", KEY_PASSWORD, "-keypass", KEY_PASSWORD,
             "-dname", "CN=Velix, OU=Velix, O=Velix, C=RU"])

    aligned = BUILD / "aligned.apk"
    run([zipalign, "-f", "4", unsigned, aligned])

    result = HERE / "Velix.apk"
    run([apksigner, "sign", "--ks", KEYSTORE, "--ks-key-alias", KEY_ALIAS,
         "--ks-pass", f"pass:{KEY_PASSWORD}", "--key-pass", f"pass:{KEY_PASSWORD}",
         "--out", result, aligned])
    run([apksigner, "verify", result])

    size = result.stat().st_size / 1024
    print(f"готово: {result} ({size:.0f} КБ)")


if __name__ == "__main__":
    main()
