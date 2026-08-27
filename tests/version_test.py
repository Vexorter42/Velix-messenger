"""Номера версий по новой схеме 0.2.2.0."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import version  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"TEST {name}: {'OK' if ok else 'FAIL' + (' — ' + str(detail) if detail else '')}")


check("version-shape", version.VERSION.count(".") == 3
      and version.VERSION.startswith("0."), version.VERSION)
check("version-leading-zero-ignored",
      version.as_tuple("0.2.2.0") == version.as_tuple("2.2.0"),
      version.as_tuple("0.2.2.0"))
check("version-patch-newer", version.is_newer("0.2.2.1", "0.2.2.0"))
check("version-minor-newer", version.is_newer("0.2.3.0", "0.2.2.1"))
check("version-same-not-newer", not version.is_newer("0.2.2.0", "0.2.2.0"))
check("version-older-not-newer", not version.is_newer("0.2.1.0", "0.2.2.0"))
check("version-letters-survive", version.is_newer("0.2.2a", "0.2.1.9"),
      version.as_tuple("0.2.2a"))
check("version-junk-is-zero", version.as_tuple("абракадабра") == (0, 0, 0),
      version.as_tuple("абракадабра"))
# Ради этого ноль и отбрасывается: клиент 2.1.0 должен увидеть обновление
check("version-old-client-updates", version.is_newer("2.2.0", "2.1.0"))

# Число в установщике и в приложении для телефона — то же самое
repo = Path(__file__).resolve().parent.parent
installer = (repo / "installer.iss").read_text(encoding="utf-8", errors="ignore")
manifest = (repo / "android" / "AndroidManifest.xml").read_text(encoding="utf-8")
check("version-installer-matches", f'"{version.VERSION}"' in installer,
      [line for line in installer.splitlines() if "AppVersion" in line][:1])
check("version-android-matches", f'versionName="{version.VERSION}"' in manifest,
      [line for line in manifest.splitlines() if "versionName" in line][:1])

print(f"\nИТОГО: {sum(results)}/{len(results)} проверок прошли")
sys.exit(0 if all(results) else 1)
