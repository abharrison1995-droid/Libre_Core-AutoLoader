from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import uuid
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoloader.rom_builder.artifact_engine import (
    ARTIFACT_RECIPES,
    COREBOOT_2609,
    HANDOFF_RECIPES,
    LIBREBOOT_2601,
    ONEVYRAIN_HANDOFF,
    X200_4MB,
    X200_8MB,
    X220_COREBOOT,
    ArtifactEngine,
    ArtifactManifest,
    BuildRequest,
    BuildStatus,
    CBFSInspectionRecord,
    CommandRecord,
    PinnedFile,
    ReproducibilityRecord,
    SourcePin,
    X200UBootDependencyEvidence,
    X200GrubFontEvidence,
    compare_region_maps,
    derive_lbmk_output_path,
    _finalize_x220_operation_plan,
    lbmk_expected_output_paths,
    parse_cbfs_parseable_output,
    parse_ifdtool_regions,
    parse_kconfig,
    select_exact_lbmk_artifact,
    validate_recipe_config,
    validate_x220_region_preservation,
    validate_x220_backup_evidence,
    _BUILD_GIT_CONFIG_BYTES,
    _BUILD_GIT_CONFIG_SHA256,
    _build_environment_record,
    _ensure_operation_git_config,
)
from autoloader.flasher.backup_service import (
    BackupManifestStore,
    BackupSetResult,
    CapturedChipBackup,
    StorageEvidence,
)
from autoloader.workflow.types import (
    BackupEvidence,
    BackupPairEvidence,
    BuildIntentEvidence,
    ChipEvidence,
    ChipTopology,
    DeviceEvidence,
    FirmwareFamily,
    ProgrammerEvidence,
    ProgrammerKind,
    RegionEvidence,
    build_intent_record,
    build_intent_sha256,
)


def _synthetic_x200_nested_repository_evidence():
    import autoloader.rom_builder.artifact_engine as module

    coreboot = module.X200NestedRepositoryEvidence(
        role="coreboot",
        relative_path="work/lbmk/src/coreboot/default",
        repository_url=module._X200_COREBOOT_ORIGIN_URL,
        cache_origin_relative_path=f"work/lbmk/{module._X200_COREBOOT_CACHE_RELATIVE_PATH}",
        base_revision=module._LBMK_COREBOOT_REVISION,
        base_tree_id=module._LBMK_COREBOOT_TREE,
        resulting_revision="a" * 40,
        resulting_tree_id=module._X200_COREBOOT_POST_TREE,
        patch_inputs=tuple(
            (f"work/lbmk/config/coreboot/default/patches/{index:04}.patch", "b" * 64)
            for index in range(module._X200_COREBOOT_PATCH_COUNT)
        ),
        patch_revisions=tuple(
            f"{index:040x}" for index in range(1, module._X200_COREBOOT_PATCH_COUNT)
        ) + ("a" * 40,),
        patch_tree_ids=("c" * 40,) * (module._X200_COREBOOT_PATCH_COUNT - 1) + (module._X200_COREBOOT_POST_TREE,),
        source_file_hashes=tuple((item.path, item.sha256) for item in module._LBMK_COREBOOT_CBFSTOOL_SOURCES),
        generated_status=(
            " M 3rdparty/libgfxinit",
            " M 3rdparty/vboot",
            "?? 3rdparty/fspcc36ae2b5775fa7400cb3282680afc0f6cb37a3c/",
            "?? util/cbfstool/fmd_parser.h",
            "?? util/cbfstool/fmd_scanner.h",
            "?? util/vboot_lib/libvboot_host.so",
        ),
        generated_files=(
            module.X200GeneratedFileEvidence(
                relative_path="util/cbfstool/fmd_parser.h",
                size_bytes=3607,
                sha256="4e93908f1854f103e832c4c115330689b4bb623f2d94ecdb2dcd7d0d6c9f609f",
                kind="copied-shipped-header",
                source_relative_path="util/cbfstool/fmd_parser.h_shipped",
                source_sha256="4e93908f1854f103e832c4c115330689b4bb623f2d94ecdb2dcd7d0d6c9f609f",
            ),
            module.X200GeneratedFileEvidence(
                relative_path="util/cbfstool/fmd_scanner.h",
                size_bytes=10277,
                sha256="c5b826d911122e6513f1f5c2ad5d30b067caf275e0a949b544b6a853a081cb46",
                kind="copied-shipped-header",
                source_relative_path="util/cbfstool/fmd_scanner.h_shipped",
                source_sha256="c5b826d911122e6513f1f5c2ad5d30b067caf275e0a949b544b6a853a081cb46",
            ),
            module.X200GeneratedFileEvidence(
                relative_path="util/vboot_lib/libvboot_host.so",
                size_bytes=120,
                sha256="d" * 64,
                kind="elf-shared-object",
                elf_class="ELF64",
                elf_type="ET_DYN",
                elf_machine="EM_X86_64",
            ),
        ),
    )
    libgfxinit = module.X200NestedRepositoryEvidence(
        role="libgfxinit",
        relative_path="work/lbmk/src/coreboot/default/3rdparty/libgfxinit",
        repository_url=module._X200_LIBGFXINIT_ORIGIN_URL,
        cache_origin_relative_path=f"work/lbmk/{module._X200_LIBGFXINIT_CACHE_RELATIVE_PATH}",
        base_revision=module._X200_LIBGFXINIT_BASE_REVISION,
        base_tree_id=module._X200_LIBGFXINIT_BASE_TREE,
        resulting_revision="e" * 40,
        resulting_tree_id=module._X200_LIBGFXINIT_POST_TREE,
        patch_inputs=tuple((f"work/lbmk/{path}", digest) for path, digest in module._X200_LIBGFXINIT_PATCH_INPUTS),
        patch_revisions=("d" * 40, "e" * 40),
        patch_tree_ids=("e" * 40, module._X200_LIBGFXINIT_POST_TREE),
    )
    vboot = module.X200NestedRepositoryEvidence(
        role="vboot",
        relative_path=module._X200_VBOOT_SOURCE_RELATIVE_PATH,
        repository_url=module._X200_VBOOT_ORIGIN_URL,
        cache_origin_relative_path=f"work/lbmk/{module._X200_VBOOT_CACHE_RELATIVE_PATH}",
        base_revision=module._X200_VBOOT_BASE_REVISION,
        base_tree_id=module._X200_VBOOT_BASE_TREE,
        resulting_revision="f" * 40,
        resulting_tree_id=module._X200_VBOOT_POST_OVERLAY_TREE,
        patch_inputs=tuple((f"work/lbmk/{path}", digest) for path, digest in module._X200_VBOOT_PATCH_STACK),
        patch_revisions=("1" * 40, "f" * 40),
        patch_tree_ids=(module._X200_VBOOT_PRE_OVERLAY_TREE, module._X200_VBOOT_POST_OVERLAY_TREE),
        source_file_hashes=((module._X200_VBOOT_SOURCE_FILE, module._X200_VBOOT_POSTIMAGE_SHA256),),
        source_file_preimage_hashes=((module._X200_VBOOT_SOURCE_FILE, module._X200_VBOOT_PREIMAGE_SHA256),),
    )
    fsp = module.X200NestedRepositoryEvidence(
        role="fsp",
        relative_path=module._X200_FSP_RELATIVE_PATH,
        repository_url=module._X200_FSP_ORIGIN_URL,
        cache_origin_relative_path=f"work/lbmk/{module._X200_FSP_CACHE_RELATIVE_PATH}",
        base_revision=module._X200_FSP_REVISION,
        base_tree_id=module._X200_FSP_TREE,
        resulting_revision=module._X200_FSP_REVISION,
        resulting_tree_id=module._X200_FSP_TREE,
        patch_inputs=(),
        patch_revisions=(),
        patch_tree_ids=(),
    )
    return (coreboot, libgfxinit, vboot, fsp)


def _minimal_test_elf_shared_object() -> bytes:
    elf = bytearray(120)
    elf[:16] = b"\x7fELF\x02\x01\x01" + (b"\x00" * 9)
    elf[16:18] = (3).to_bytes(2, "little")
    elf[18:20] = (62).to_bytes(2, "little")
    elf[20:24] = (1).to_bytes(4, "little")
    elf[32:40] = (64).to_bytes(8, "little")
    elf[52:54] = (64).to_bytes(2, "little")
    elf[54:56] = (56).to_bytes(2, "little")
    elf[56:58] = (1).to_bytes(2, "little")
    elf[64:68] = (1).to_bytes(4, "little")
    elf[72:80] = (120).to_bytes(8, "little")
    elf[80:88] = (120).to_bytes(8, "little")
    return bytes(elf)


def _write_test_generated_coreboot_tree(root: Path) -> Path:
    import autoloader.rom_builder.artifact_engine as module

    checkout = root / "work" / "lbmk" / "src" / "coreboot" / "default"
    gitlink_root = checkout / "3rdparty"
    gitlink_root.mkdir(parents=True, exist_ok=True)
    (gitlink_root / "libgfxinit").write_text("base libgfxinit placeholder\n", encoding="utf-8")
    (gitlink_root / "vboot").write_text("base vboot placeholder\n", encoding="utf-8")
    fixture_dir = Path(__file__).parent / "fixtures" / "x200_coreboot_generated"
    for _output_relative, source_relative, _expected_hash in module._X200_COREBOOT_GENERATED_HEADER_SOURCES:
        source = checkout.joinpath(*Path(source_relative).parts)
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes((fixture_dir / Path(source_relative).name.removesuffix("_shipped")).read_bytes())
    git = ["git", "-C", str(checkout)]
    subprocess.run([*git, "init", "--quiet"], check=True, capture_output=True, text=True)
    subprocess.run([*git, "config", "user.name", "X200 fixture"], check=True)
    subprocess.run([*git, "config", "user.email", "x200-fixture@example.invalid"], check=True)
    excludes = checkout / ".git" / "empty-excludes"
    excludes.write_text("", encoding="utf-8")
    subprocess.run([*git, "config", "core.excludesFile", str(excludes)], check=True)
    subprocess.run([*git, "config", "core.autocrlf", "false"], check=True)
    subprocess.run([*git, "config", "commit.gpgsign", "false"], check=True)
    subprocess.run([*git, "add", "3rdparty/libgfxinit", "3rdparty/vboot", "util/cbfstool/*_shipped"], check=True)
    subprocess.run([*git, "commit", "--quiet", "-m", "synthetic status baseline"], check=True)
    (gitlink_root / "libgfxinit").write_text("patched libgfxinit placeholder\n", encoding="utf-8")
    (gitlink_root / "vboot").write_text("patched vboot placeholder\n", encoding="utf-8")
    fsp = gitlink_root / "fspcc36ae2b5775fa7400cb3282680afc0f6cb37a3c"
    fsp.mkdir()
    subprocess.run(["git", "-C", str(fsp), "init", "--quiet"], check=True)
    for output_relative, source_relative, _expected_hash in module._X200_COREBOOT_GENERATED_HEADER_SOURCES:
        output = checkout.joinpath(*Path(output_relative).parts)
        source = checkout.joinpath(*Path(source_relative).parts)
        source.parent.mkdir(parents=True, exist_ok=True)
        output.parent.mkdir(parents=True, exist_ok=True)
        data = (fixture_dir / Path(output_relative).name).read_bytes()
        source.write_bytes(data)
        output.write_bytes(data)
    library = checkout.joinpath(*Path(module._X200_COREBOOT_HOST_ELF).parts)
    library.parent.mkdir(parents=True, exist_ok=True)
    library.write_bytes(_minimal_test_elf_shared_object())
    library.chmod(0o755)
    return checkout


def test_pins_are_full_and_recipe_allowlist_is_exact() -> None:
    import autoloader.rom_builder.artifact_engine as module

    assert COREBOOT_2609.revision == "8b0e34f90e08547cf19e550a43ed72f69c39d6ed"
    assert COREBOOT_2609.tree_id == "1cb70fc2c4a0229fa17991b19cf6916b71f75ba7"
    assert LIBREBOOT_2601.revision == "1cf3181537b8d1fe1df0e91681f700850a3d9bf6"
    assert LIBREBOOT_2601.tree_id == "0cb9451c6e2e265d3aa5b7e371752d45cab98f53"
    assert all(len(pin.revision) == 40 and len(pin.tree_id) == 40 for pin in (COREBOOT_2609, LIBREBOOT_2601))
    assert set(ARTIFACT_RECIPES) == {"x200-libreboot-4mb", "x200-libreboot-8mb", "x220-coreboot-8mb-seabios"}
    assert set(HANDOFF_RECIPES) == {"t430-1vyrain-upstream-handoff"}
    assert X200_4MB.expected_size_bytes == 4 * 1024 * 1024
    assert X200_8MB.expected_size_bytes == 8 * 1024 * 1024
    assert X220_COREBOOT.expected_size_bytes == 8 * 1024 * 1024
    assert ONEVYRAIN_HANDOFF.source.repository_url == "https://github.com/n4ru/1vyrain.git"
    assert {item.path for item in LIBREBOOT_2601.files} >= {
        "config/coreboot/default/target.cfg",
        "config/git/coreboot/pkg.cfg",
        "include/lib.sh",
    }
    assert {item.path for item in COREBOOT_2609.files} >= {
        "util/cbfstool/cbfs_image.c",
        "util/cbfstool/cbfstool.c",
        "src/Kconfig",
    }
    assert module._LBMK_COREBOOT_REVISION == "fcd716d9a272461da1fdbaf1c048eb5a52c7896b"
    assert module._LBMK_COREBOOT_TREE == "e16fe240567cb3fa08619c6a23e51c4af142f6ac"
    assert tuple(item.sha256 for item in module._LBMK_COREBOOT_CBFSTOOL_SOURCES) == (
        "fd6a56a8e20c15f4fd8f03511f05ce8264f120ad0a0dc11900e2e04f89a88dda",
        "0cfd0e5cfb4b169355508e270b1cff1f644010cda72bd36a9a8f07952377e3cb",
    )
    assert module._X200_VBOOT_OVERLAY_SHA256 == "172c41a5b92557de0739ec01ec2f37331c9066c8896fbfa3829be89f45bcfaf7"
    assert module._X200_VBOOT_PREIMAGE_SHA256 == "643cbe79bc91455013f23521f646fbc31d44cbc31f0758f57cd9680de6a8e6a4"
    assert module._X200_VBOOT_POSTIMAGE_SHA256 == "32f09d3948d5c9471c966767a990545cd2f3293491118ad1ef3f510f62462245"
    assert {item.path: item.sha256 for item in LIBREBOOT_2601.files} == {
        "include/rom.sh": "9b04ea21df571682ccbc1a66e19d17ca9fe6ccde30a8089d1cb45e3683283e3b",
        "include/get.sh": "b473d8af1ae2fea9663103aad12a191d297611d65d50f89e04bd8de6e561feb9",
        "config/git/u-boot/pkg.cfg": "84088781b2b1630ffc95a5024945986f2ff8c83cc3cc86e474eef52a2f324ce0",
        "config/u-boot/x86_64/target.cfg": "98d8b99e09fc64257aa6d2dfc2d470123e22d42bca718dd34aae107a09f71c9f",
        "config/u-boot/amd64coreboot/target.cfg": "b7198cb054718c73194c3e7d193a758469c8dac64b0dc2bc5abd0fdb9746afde",
        "config/u-boot/amd64coreboot/config/default": "16c6839809a9755a39d9674216b40b0b9f299c4869e7a3448ad755f3eec0ae6a",
        "config/u-boot/x86_64/patches/0001-x86-serial-ns16550-Allow-the-UART-to-be-silently-dis.patch": "03b77b02eaf90bcb35e6fb0c5ba1afb749241a14a94be8a87606a1df9386fa85",
        "config/u-boot/x86_64/patches/0002-x86-Avoid-clearing-the-VESA-display.patch": "f7bff931f1f0735d63eb86bfd4a53cd6e93c4fdaf25d04bee804c5c3f64b176e",
        "config/u-boot/x86_64/patches/0003-x86-Avoid-clearing-the-VESA-display.patch": "e40b5e133de50befff9b1b1738e20420c0e86873668bed1ae3aec1b309bebf56",
        "config/u-boot/x86_64/patches/0004-Support-auto-boot-timeout-delay-bootflow-menu.patch": "36b864aad951ecdb5680ca1d4d87bdf9dfbe82511be840664fbbd34ef8f67b53",
        "config/u-boot/x86_64/patches/0005-Libreboot-branding-version-on-the-bootflow-menu.patch": "df7ff820dbe9ebea8b2b19ac7c7f48d2342c1a3e525bbe51644c35873c758081",
        "config/u-boot/x86_64/patches/0006-i-made-it-purple.patch": "fdf1baab5844be0a6ed8068afeeb4589fc0e346d72850f2232dc42dd1b007471",
        "config/u-boot/x86_64/patches/0008-scripts-dtc-pylibfdt-libfdt.i_shipped-Use-SWIG_Appen.patch": "91ad0bae21b0f459e49e4edebdbd17993af7efffabe379287fb1089d917f77d5",
        "config/coreboot/x200_4mb/target.cfg": "74e0db657f688a39dcc581b4209f786da5397b5bb89920bfb8659a3650b7c34b",
        "config/coreboot/x200_4mb/config/libgfxinit_corebootfb": "4038c9b655c1c74c582cb89a9f59688308d47faf8069679adce1a06da756410c",
        "config/coreboot/x200_4mb/config/libgfxinit_txtmode": "76d49f1b10c3b45c65fd9149263e528aa60bbddf1d4ea4258835d9410a35cc65",
        "config/coreboot/x200_8mb/target.cfg": "74e0db657f688a39dcc581b4209f786da5397b5bb89920bfb8659a3650b7c34b",
        "config/coreboot/x200_8mb/config/libgfxinit_corebootfb": "6435d325d4f77c18ec6b411d708508d4eda2e5a4d2f6b83dfd3c0c0d230efac4",
        "config/coreboot/x200_8mb/config/libgfxinit_txtmode": "d0558fb2221060c7062bd225b30ab65c71d994c3d1dcad98240440475062edaf",
        "config/coreboot/default/target.cfg": "b0fa4882ec69979345e117076dda3c751143cd7d720e9cb4651796f77b2c395e",
        "config/git/coreboot/pkg.cfg": "82e283bbd1c2b60ec9b2742e5e8c0a108dca8fe8ea483283a705b6102238c42f",
        "include/lib.sh": "f429d5c14be6e988f7472d1f02e19f893be467f1685ad5a5822f41000e47754a",
        "config/submodule/coreboot/default/libgfxinit/module.cfg": "d991d35b81f20012cbae7fb9f7fcd8ca55f4e7bb13870ba104b4785bfb2864ad",
        "config/submodule/coreboot/default/libgfxinit/patches/0001-g45-hw-gfx-gma-plls.adb-Make-reference-clock-frequen.patch": "5cb94277754f1711d25c4ddce8c4e5de48afc004ea718bc9e8ff799e6ad29db3",
        "config/submodule/coreboot/default/libgfxinit/patches/0002-re-try-EDID-reading-when-it-fails.patch": "cbe477a3fb1a64082754269453d8bd98557c93aa0a90931ae251a44dd7d73def",
        "config/submodule/coreboot/default/vboot/module.cfg": "655585bc4f884c52bdf93b6685e6aa073a92adfa39d96ee73f33ba5ec31aea48",
        "config/submodule/coreboot/default/vboot/patches/0001-extract_vmlinuz.c-Fix-the-bounds-check-on-vmlinuz_he.patch": "5008f31ddb78d77fdceb27412d2d56844676b4eb48150fdea8575caf513d8543",
        "config/submodule/coreboot/default/fsp/module.cfg": "8d54dcc9ea20155ac2ff18e37adbe60b58820c4b5009cdf07e46ee6c9be652e0",
        "config/git/grub/pkg.cfg": "6e8886854534db158f8560d7ec99e6eccd2f2c4ca6100aa0c9442c22558081c8",
        "config/grub/default/target.cfg": "a23bb6648ce1f893504c19d957a53de9de8a861f92ae123ade094dade4e8f5a8",
        "include/tree.sh": "de3c294c4401fcc5a6364e644364127e3ddb31d3f0d7b00050350516ae428876",
        "config/grub/default/patches/0001-mitigate-grub-s-missing-characters-for-borders-arrow.patch": "049c385db82d971343e769520964bf557789f727ab0ab7d2d95ed2289c88aac0",
        "config/grub/default/patches/0002-say-the-name-libreboot-in-the-grub-menu.patch": "93ba1fe44bb731049160fc7ab87641c544cada6741b81b1463f53cb7226fbeb1",
        "config/grub/default/patches/0003-at_keyboard-coreboot-force-scancodes2-translate.patch": "40dd3c99aa16cea84e44dde75596545a566bf9ff9de08aec31b4f7ca0ac5e44c",
        "config/grub/default/patches/0004-keylayouts-don-t-print-Unknown-key-message.patch": "1a0bacbfa5191540d3487e1ebac848e19424a1b15cff9c44e9d4526a8d9a4017",
        "config/grub/default/patches/0005-don-t-print-missing-prefix-errors-on-the-screen.patch": "fdc6501d624697e4d8c2bfdb26cb5ebbecd1aeb566343e273a169f39dda5c8cd",
        "config/grub/default/patches/0006-don-t-print-error-if-module-not-found.patch": "dacbcf7157605a95c1bf3f974aba41d6f6e70a9b52de4670688cf543bbd50799",
        "config/grub/default/patches/0007-don-t-print-empty-error-messages.patch": "ee48e0449e968a28789da04904e7e2d6a58f698b66ff2b8864610a90848cd4df",
        "config/grub/default/patches/0008-kern-coreboot-mmap-Map-to-reserved.patch": "203300078af56bfcc6b9e7958c0cdb24c3827873c3e1b0cd36eb9ce3db712504",
        "config/grub/default/patches/0009-Revert-configure-Check-linker-for-image-base-support.patch": "71b838b5f9796954e156400100ef2503c04659f0cec04df172c20611a3cf9945",
        "config/grub/default/patches/0010-Revert-configure-Print-a-more-helpful-error-if-autoc.patch": "4a00cc1ab2b3c04d0251a50f7309a4b82c9943a901ef92207f54baae346d46d6",
    }
    assert X200_4MB.expected_config_sha256 == "4038c9b655c1c74c582cb89a9f59688308d47faf8069679adce1a06da756410c"


def test_x200_nested_repository_manifest_contract_binds_generated_trees_and_patch_inputs() -> None:
    import autoloader.rom_builder.artifact_engine as module

    rows = _synthetic_x200_nested_repository_evidence()
    module._validate_x200_nested_repository_evidence(rows)

    vboot = rows[2]
    assert module._X200_VBOOT_ORIGIN_URL == "https://review.coreboot.org/vboot.git"
    assert module._X200_VBOOT_CACHE_RELATIVE_PATH == "cache/clone/review.coreboot.org/vboot.git"
    mutations = (
        replace(vboot, repository_url="https://github.com/coreboot/vboot"),
        replace(vboot, repository_url="https://attacker.invalid/vboot.git"),
        replace(vboot, cache_origin_relative_path="work/lbmk/cache/clone/github.com/coreboot/vboot"),
        replace(vboot, cache_origin_relative_path="work/lbmk/cache/clone/attacker.invalid/vboot.git"),
        replace(vboot, resulting_tree_id="2" * 40),
        replace(vboot, patch_inputs=(vboot.patch_inputs[0], (vboot.patch_inputs[1][0], "9" * 64))),
        replace(vboot, source_file_hashes=((module._X200_VBOOT_SOURCE_FILE, "8" * 64),)),
        replace(vboot, source_file_preimage_hashes=((module._X200_VBOOT_SOURCE_FILE, "7" * 64),)),
        replace(vboot, generated_status=(" M unexpected.c",)),
    )
    for changed in mutations:
        with pytest.raises(ValueError, match="provenance|source state"):
            module._validate_x200_nested_repository_evidence((rows[0], rows[1], changed, rows[3]))

    changed_coreboot = replace(rows[0], resulting_tree_id="7" * 40)
    with pytest.raises(ValueError, match="Coreboot source provenance"):
        module._validate_x200_nested_repository_evidence((changed_coreboot, rows[1], rows[2], rows[3]))
    with pytest.raises(ValueError, match="final revision"):
        replace(vboot, patch_revisions=("1" * 40, "2" * 40))


def test_x200_generated_file_manifest_requires_exact_typed_inventory() -> None:
    import autoloader.rom_builder.artifact_engine as module

    rows = _synthetic_x200_nested_repository_evidence()
    module._validate_x200_nested_repository_evidence(rows)
    coreboot = rows[0]
    with pytest.raises(ValueError, match="generated-file inventory"):
        module._validate_x200_nested_repository_evidence((replace(coreboot, generated_files=()), *rows[1:]))
    with pytest.raises(ValueError, match="generated-file inventory"):
        extra = (*coreboot.generated_files, coreboot.generated_files[-1])
        module._validate_x200_nested_repository_evidence((replace(coreboot, generated_files=extra), *rows[1:]))
    with pytest.raises(ValueError, match="source provenance"):
        module._validate_x200_nested_repository_evidence((
            replace(coreboot, generated_status=coreboot.generated_status[:-1]), *rows[1:]
        ))
    with pytest.raises(ValueError, match="shipped source"):
        changed_header = replace(coreboot.generated_files[0], sha256="e" * 64)
        module._validate_x200_nested_repository_evidence((
            replace(coreboot, generated_files=(changed_header, *coreboot.generated_files[1:])), *rows[1:]
        ))
    with pytest.raises(ValueError, match="host library"):
        changed_elf = replace(coreboot.generated_files[-1], elf_machine="EM_AARCH64")
        module._validate_x200_nested_repository_evidence((
            replace(coreboot, generated_files=(*coreboot.generated_files[:-1], changed_elf)), *rows[1:]
        ))
    with pytest.raises(ValueError, match="unrelated nested repository"):
        replace(rows[1], generated_files=coreboot.generated_files)


@pytest.mark.parametrize("mutation", [
    "missing", "directory", "symlink", "symlink-directory", "modified-header", "bad-elf", "bad-elf-bounds"
])
def test_x200_generated_file_capture_rejects_unsafe_or_changed_outputs(tmp_path: Path, mutation: str) -> None:
    import autoloader.rom_builder.artifact_engine as module

    checkout = _write_test_generated_coreboot_tree(tmp_path)
    target = checkout / "util" / "cbfstool" / "fmd_parser.h"
    library = checkout / "util" / "vboot_lib" / "libvboot_host.so"
    if mutation == "missing":
        target.unlink()
    elif mutation == "directory":
        target.unlink()
        target.mkdir()
    elif mutation == "symlink":
        target.unlink()
        try:
            target.symlink_to(target.with_name("fmd_parser.h_shipped"))
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"Host cannot create symlinks for this negative case: {exc}")
    elif mutation == "symlink-directory":
        util = checkout / "util"
        relocated = tmp_path / "outside-util"
        util.rename(relocated)
        try:
            util.symlink_to(relocated, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"Host cannot create directory symlinks for this negative case: {exc}")
    elif mutation == "modified-header":
        target.write_bytes(target.read_bytes() + b"\nchanged\n")
    else:
        library_data = bytearray(_minimal_test_elf_shared_object())
        if mutation == "bad-elf":
            library_data[:4] = b"NOPE"
        else:
            library_data[32:40] = ((1 << 63) - 1).to_bytes(8, "little")
        library.write_bytes(library_data)
        library.chmod(0o755)
    with pytest.raises(ValueError):
        module._capture_x200_coreboot_generated_files(checkout)


def test_x200_generated_file_inventory_rehashes_status_and_bytes_at_reopen(tmp_path: Path) -> None:
    import autoloader.rom_builder.artifact_engine as module

    checkout = _write_test_generated_coreboot_tree(tmp_path)
    coreboot = _synthetic_x200_nested_repository_evidence()[0]
    evidence = replace(coreboot, generated_files=module._capture_x200_coreboot_generated_files(checkout))
    operation = tmp_path
    module._verify_x200_coreboot_generated_files(operation, evidence)
    library = checkout / "util" / "vboot_lib" / "libvboot_host.so"
    library.write_bytes(library.read_bytes() + b"mutation")
    with pytest.raises(ValueError, match="bytes differ"):
        module._verify_x200_coreboot_generated_files(operation, evidence)
    library.write_bytes(_minimal_test_elf_shared_object())
    (checkout / "unexpected-generated-output.h").write_text("unexpected\n", encoding="utf-8")
    with pytest.raises(ValueError, match="status changed"):
        module._verify_x200_coreboot_generated_files(operation, evidence)
    (checkout / "unexpected-generated-output.h").unlink()
    shipped = checkout / "util" / "cbfstool" / "fmd_parser.h_shipped"
    shipped.write_bytes(shipped.read_bytes() + b"unexpected tracked change")
    with pytest.raises(ValueError, match="status changed"):
        module._verify_x200_coreboot_generated_files(operation, evidence)


def test_x200_vboot_overlay_is_packaged_and_matches_reviewed_evidence_copy() -> None:
    import importlib.resources
    import autoloader.rom_builder.artifact_engine as module

    packaged = importlib.resources.files("autoloader.rom_builder").joinpath(
        "patches", module._X200_VBOOT_OVERLAY_RESOURCE
    ).read_bytes()
    evidence_copy = (
        Path(__file__).parents[1]
        / "docs"
        / "v1"
        / "evidence"
        / "x200-const-compat-overlay-20261021"
        / "0002-vboot-cbfstool-keep-strchr-result-const.patch"
    )
    assert hashlib.sha256(packaged).hexdigest() == module._X200_VBOOT_OVERLAY_SHA256
    assert evidence_copy.read_bytes() == packaged


def test_legacy_schema_three_cannot_reopen_as_complete_x200() -> None:
    import json
    import autoloader.rom_builder.artifact_engine as module

    failed = ArtifactManifest(
        schema_version=3,
        operation_id="legacy-x200-operation",
        created_at_utc="2026-10-10T00:00:00+00:00",
        recipe_id="x200-libreboot-4mb",
        workflow_id="x200-libreboot",
        status="failed",
        source_repository_url=LIBREBOOT_2601.repository_url,
        source_revision=LIBREBOOT_2601.revision,
        source_tree_id=LIBREBOOT_2601.tree_id,
        source_files=(),
        commands=(),
        host_platform="test host",
        toolchain=(),
        build_environment=(),
        container_image_digest=None,
        configuration_sha256="a" * 64,
        resolved_configuration_sha256=None,
        observed_chip_capacity_bytes=X200_4MB.expected_size_bytes,
        expected_regions=(),
        blob_sha256=(),
        artifacts=(),
        selected_artifact_relative_path=None,
        selected_artifact_sha256=None,
        failure_code="HISTORICAL_FIXTURE",
        failure_detail="Synthetic legacy manifest; no build or hardware operation.",
        reproducibility=ReproducibilityRecord("not_attempted", None, None, "No build run."),
    )
    cbfs = module.CBFSInspectionRecord(
        tool_relative_path="elf/coreboot/default/cbfstool",
        tool_sha256="b" * 64,
        source_repository_url=LIBREBOOT_2601.repository_url,
        source_revision=LIBREBOOT_2601.revision,
        source_tree_id=LIBREBOOT_2601.tree_id,
        region="whole_flash",
        required_payload="fallback/payload",
        inventory=(module.CBFSEntry("fallback/payload", 0, "payload", 0, 1, 1),),
        source_file_hashes=(("util/cbfstool/cbfs_image.c", "c" * 64),),
    )
    selected = module.ArtifactRecord(
        relative_path="artifacts/x200.rom",
        sha256="d" * 64,
        size_bytes=1,
        regions=(),
        source_relative_path="bin/x200.rom",
        cbfs_inspection=cbfs,
    )
    roundtrip_manifest = replace(
        failed,
        schema_version=4,
        artifacts=(replace(
            selected,
            cbfs_inspection=replace(
                cbfs,
                nested_repositories=_synthetic_x200_nested_repository_evidence(),
            ),
        ),),
    )
    reopened = module._artifact_manifest_from_mapping(json.loads(roundtrip_manifest.canonical_json()))
    assert reopened.artifacts[0].cbfs_inspection is not None
    assert reopened.artifacts[0].cbfs_inspection.nested_repositories == _synthetic_x200_nested_repository_evidence()

    missing = json.loads(roundtrip_manifest.canonical_json())
    missing["artifacts"][0]["cbfs_inspection"]["nested_repositories"][0].pop("generated_files")
    missing_manifest = module._artifact_manifest_from_mapping(missing)
    assert missing_manifest.artifacts[0].cbfs_inspection is not None
    with pytest.raises(ValueError, match="generated-file inventory"):
        module._validate_x200_nested_repository_evidence(
            missing_manifest.artifacts[0].cbfs_inspection.nested_repositories
        )

    tampered = json.loads(roundtrip_manifest.canonical_json())
    tampered["artifacts"][0]["cbfs_inspection"]["nested_repositories"][0]["generated_files"][0]["sha256"] = "e" * 64
    tampered_manifest = module._artifact_manifest_from_mapping(tampered)
    assert tampered_manifest.artifacts[0].cbfs_inspection is not None
    with pytest.raises(ValueError, match="shipped source"):
        module._validate_x200_nested_repository_evidence(
            tampered_manifest.artifacts[0].cbfs_inspection.nested_repositories
        )

    with pytest.raises(ValueError, match="schema 4 nested lbmk source provenance"):
        replace(
            failed,
            status="complete",
            failure_code=None,
            artifacts=(selected,),
            selected_artifact_relative_path=selected.relative_path,
            selected_artifact_sha256=selected.sha256,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("revision", "1" * 40),
        ("tree_id", "2" * 40),
        ("origin", "https://attacker.invalid/lbmk.git"),
        ("status", (" M include/tree.sh", "?? unexpected.patch", "?? config/u-boot/x86_64/patches/0009-x86-use-output-target-for-efi.patch", "?? config/submodule/coreboot/default/vboot/patches/0002-vboot-cbfstool-keep-strchr-result-const.patch")),
    ],
)
def test_x200_lbmk_overlay_root_state_rejects_parent_drift(field: str, value: object) -> None:
    import autoloader.rom_builder.artifact_engine as module

    fields: dict[str, object] = {
        "revision": LIBREBOOT_2601.revision,
        "tree_id": LIBREBOOT_2601.tree_id,
        "origin": LIBREBOOT_2601.repository_url,
        "status": (
            " M include/tree.sh",
            "?? config/u-boot/x86_64/patches/0009-x86-use-output-target-for-efi.patch",
            "?? config/submodule/coreboot/default/vboot/patches/0002-vboot-cbfstool-keep-strchr-result-const.patch",
        ),
    }
    module._validate_x200_lbmk_overlay_root_state(**fields, after_build=True)  # type: ignore[arg-type]
    fields[field] = value
    with pytest.raises(ValueError, match="identity/status"):
        module._validate_x200_lbmk_overlay_root_state(**fields, after_build=True)  # type: ignore[arg-type]


def test_git_value_preserves_leading_porcelain_status_column(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess
    from types import SimpleNamespace

    import autoloader.rom_builder.artifact_engine as module

    porcelain = (
        " M include/tree.sh\n"
        "?? config/u-boot/x86_64/patches/0009-x86-use-output-target-for-efi.patch\n"
        "?? config/submodule/coreboot/default/vboot/patches/0002-vboot-cbfstool-keep-strchr-result-const.patch\n"
    )

    def fake_run(argv: list[str], **kwargs: object) -> SimpleNamespace:
        assert argv == [
            "git", "-C", str(tmp_path), "status", "--porcelain=v1", "--untracked-files=all"
        ]
        assert kwargs["timeout"] == 30
        return SimpleNamespace(returncode=0, stdout=porcelain)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    observed = module.ArtifactEngine._git_value(
        tmp_path, "status", "--porcelain=v1", "--untracked-files=all"
    )
    assert observed == porcelain.rstrip("\r\n")
    module._validate_x200_lbmk_overlay_root_state(
        revision=module.LIBREBOOT_2601.revision,
        tree_id=module.LIBREBOOT_2601.tree_id,
        origin=module.LIBREBOOT_2601.repository_url,
        status=tuple(filter(None, observed.splitlines())),
        after_build=True,
    )


def test_x200_vboot_overlay_bytes_allow_only_pinned_const_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autoloader.rom_builder.artifact_engine as module

    before = b"static void f(void) {\n\tchar *end = strchr(start, '\\n');\n}\n"
    after = before.replace(b"char *end", b"const char *end", 1)
    patch = (
        Path(module.__file__).parent / "patches" / module._X200_VBOOT_OVERLAY_RESOURCE
    ).read_bytes()
    monkeypatch.setattr(module, "_X200_VBOOT_PREIMAGE_SHA256", hashlib.sha256(before).hexdigest())
    monkeypatch.setattr(module, "_X200_VBOOT_POSTIMAGE_SHA256", hashlib.sha256(after).hexdigest())
    module._validate_x200_vboot_overlay_bytes(patch, before, after)
    with pytest.raises(ValueError, match="patch bytes"):
        module._validate_x200_vboot_overlay_bytes(patch + b"unexpected", before, after)
    with pytest.raises(ValueError, match="postimage"):
        module._validate_x200_vboot_overlay_bytes(patch, before, after + b"extra")
    with pytest.raises(ValueError, match="postimage"):
        module._validate_x200_vboot_overlay_bytes(patch, before.replace(b"strchr", b"memchr"), after)


def test_pinned_lbmk_cbfstool_path_matches_exact_26_01_source() -> None:
    import autoloader.rom_builder.artifact_engine as module

    accepted = 'cbfstool="elf/coreboot/default/cbfstool"\n'
    module.ArtifactEngine._validate_lbmk_cbfstool_path_selection(accepted)
    for changed in (
        'cbfstool="elf/coreboot/$tree/cbfstool"\n',
        'cbfstool="elf/coreboot/other/cbfstool"\n',
        'cbfstool="elf/coreboot/default/cbfstool"\ncbfstool="elf/coreboot/default/cbfstool"\n',
        'cbfstool="elf/coreboot/default/cbfstool-extra"\n',
    ):
        with pytest.raises(ValueError, match="cbfstool path selection changed"):
            module.ArtifactEngine._validate_lbmk_cbfstool_path_selection(changed)


def test_x200_grub_font_pins_and_configure_handoff_are_exact(tmp_path: Path) -> None:
    import autoloader.rom_builder.artifact_engine as module

    overlay = Path(module.__file__).parent / "patches" / module._X200_GRUB_FONT_OVERLAY_RESOURCE
    assert overlay.is_file() and not overlay.is_symlink()
    assert hashlib.sha256(overlay.read_bytes()).hexdigest() == module._X200_GRUB_FONT_OVERLAY_SHA256
    assert module._X200_GRUB_CONFIGURE_AC_SHA256 == "48c4580fd364054b725eb6b66307b4f326d12471e43883596fdb1508fded8a33"

    staged_font = tmp_path / "operation" / "inputs" / "unifont.otf"
    argv = (
        "./configure",
        f"--with-unifont={staged_font.as_posix()}",
        *module._X200_GRUB_CONFIGURE_FIXED_ARGS,
    )
    config_log = "configure invocation:\n  $ " + " ".join(argv) + "\n"
    assert module._parse_grub_configure_argv(config_log, staged_font) == argv
    with pytest.raises(ValueError, match="exact staged Unifont"):
        module._parse_grub_configure_argv(
            config_log.replace(staged_font.as_posix(), "/usr/share/fonts/unifont.otf"), staged_font
        )
    with pytest.raises(ValueError, match="exactly one"):
        module._parse_grub_configure_argv(config_log + config_log, staged_font)


def test_x200_grub_font_evidence_rejects_version_hash_and_source_mutations() -> None:
    from autoloader.rom_builder.artifact_engine import X200GrubFontEvidence

    import autoloader.rom_builder.artifact_engine as module

    base = X200GrubFontEvidence(
        source_font_path=module._X200_GRUB_FONT_SOURCE_PATH,
        source_font_sha256=module._X200_GRUB_FONT_SOURCE_SHA256,
        source_package=module._X200_GRUB_FONT_PACKAGE,
        source_package_version=module._X200_GRUB_FONT_PACKAGE_VERSION,
        staged_font_relative_path=module._X200_GRUB_FONT_STAGED_RELATIVE_PATH,
        staged_font_sha256=module._X200_GRUB_FONT_SOURCE_SHA256,
        overlay_resource_path=module._X200_GRUB_FONT_OVERLAY_PATH,
        overlay_resource_sha256=module._X200_GRUB_FONT_OVERLAY_SHA256,
        lbmk_tree_sh_sha256=module._X200_LBMK_TREE_SH_SHA256,
        lbmk_tree_sh_patched_sha256=module._X200_LBMK_TREE_SH_PATCHED_SHA256,
        grub_source_relative_path="work/lbmk/src/grub/default",
        grub_repository_url=module._X200_GRUB_SOURCE_URL,
        grub_cache_relative_path=module._X200_GRUB_CACHE_RELATIVE_PATH,
        grub_base_revision=module._X200_GRUB_BASE_REVISION,
        grub_base_tree_id=module._X200_GRUB_BASE_TREE,
        grub_patch_inputs=module._X200_GRUB_PATCH_INPUTS,
        grub_patch_commits=tuple(f"{index:040x}" for index in range(1, 11)),
        grub_patch_tree_ids=module._X200_GRUB_PATCH_TREE_IDS,
        grub_result_revision=f"{10:040x}",
        grub_result_tree_id=module._X200_GRUB_PATCHED_TREE,
        configure_log_relative_path=module._X200_GRUB_CONFIG_LOG_RELATIVE_PATH,
        configure_log_sha256="a" * 64,
        configure_argv=(
            "./configure",
            "--with-unifont=<operation-output-directory>/inputs/unifont.otf",
            *module._X200_GRUB_CONFIGURE_FIXED_ARGS,
        ),
        generated_payload_relative_path=module._X200_GRUB_PAYLOAD_RELATIVE_PATH,
        generated_payload_sha256="b" * 64,
    )
    assert base.source_package_version == "1:16.0.04-1build1"
    with pytest.raises(ValueError, match="pinned lbmk/GRUB recipe"):
        replace(base, source_package_version="1:16.0.05-1")
    with pytest.raises(ValueError, match="pinned lbmk/GRUB recipe"):
        replace(base, source_font_sha256="c" * 64)
    with pytest.raises(ValueError, match="pinned lbmk/GRUB recipe"):
        replace(base, grub_result_tree_id="d" * 40)
    changed_inputs = list(module._X200_GRUB_PATCH_INPUTS)
    changed_inputs[0] = (changed_inputs[0][0], "e" * 64)
    with pytest.raises(ValueError, match="pinned lbmk/GRUB recipe"):
        replace(base, grub_patch_inputs=tuple(changed_inputs))


def test_x200_manifest_font_reopen_uses_portable_operation_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reopen validates the staged bytes without requiring Ubuntu's source path."""
    import autoloader.rom_builder.artifact_engine as module
    from tests.test_x200_libreboot_workflow import _synthetic_grub_font_evidence

    font_bytes = b"Synthetic OTF fixture for manifest portability; no build is run.\n"
    font_sha256 = hashlib.sha256(font_bytes).hexdigest()
    absent_source = tmp_path / "build-host" / "usr" / "share" / "fonts" / "unifont.otf"
    monkeypatch.setattr(module, "_X200_GRUB_FONT_SOURCE_PATH", str(absent_source))
    monkeypatch.setattr(module, "_X200_GRUB_FONT_SOURCE_SHA256", font_sha256)

    evidence = _synthetic_grub_font_evidence()
    operation = tmp_path / "persisted-operation"
    staged = operation / "inputs" / "unifont.otf"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(font_bytes)
    assert not absent_source.exists()

    validated = module.ArtifactEngine._validate_x200_grub_font_staged_input(operation, evidence)
    assert validated == staged
    assert hashlib.sha256(validated.read_bytes()).hexdigest() == evidence.source_font_sha256

    staged.unlink()
    with pytest.raises(FileNotFoundError):
        module.ArtifactEngine._validate_x200_grub_font_staged_input(operation, evidence)
    staged.write_bytes(font_bytes + b"mutated")
    with pytest.raises(ValueError, match="copy changed"):
        module.ArtifactEngine._validate_x200_grub_font_staged_input(operation, evidence)


def test_x200_grub_commit_ids_may_vary_when_pinned_patch_trees_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autoloader.rom_builder.artifact_engine as module

    git = shutil.which("git")
    if git is None:
        pytest.skip("UNVALIDATED: Git executable unavailable for generated commit-ID test")

    def git_output(repository: Path, *args: str, env: dict[str, str] | None = None) -> str:
        result = subprocess.run(
            [git, "-C", str(repository), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    def create_chain(
        repository: Path,
        committer_year: int,
        altered_patch_index: int | None = None,
    ) -> tuple[str, str, tuple[str, ...], tuple[str, ...]]:
        repository.mkdir()
        subprocess.run([git, "init", str(repository)], check=True, capture_output=True, timeout=15)
        base_env = os.environ.copy()
        base_env.update({
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_DATE": "2020-01-01T00:00:00+0000",
            "GIT_COMMITTER_DATE": "2020-01-01T00:00:00+0000",
        })
        (repository / "state.txt").write_text("pinned base\n", encoding="utf-8", newline="\n")
        git_output(repository, "add", "state.txt", env=base_env)
        subprocess.run(
            [git, "-C", str(repository), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "base"],
            check=True,
            capture_output=True,
            env=base_env,
            timeout=15,
        )
        base_revision = git_output(repository, "rev-parse", "HEAD")
        base_tree = git_output(repository, "rev-parse", "HEAD^{tree}")
        commits: list[str] = []
        trees: list[str] = []
        for index in range(10):
            state = f"patch {index}\n"
            if index == altered_patch_index:
                state = f"altered patch {index}\n"
            (repository / "state.txt").write_text(state, encoding="utf-8", newline="\n")
            env = base_env.copy()
            env["GIT_AUTHOR_DATE"] = f"2020-01-02T00:00:{index:02d}+0000"
            env["GIT_COMMITTER_DATE"] = f"{committer_year}-01-02T00:00:{index:02d}+0000"
            git_output(repository, "add", "state.txt", env=env)
            subprocess.run(
                [git, "-C", str(repository), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", f"patch-{index}"],
                check=True,
                capture_output=True,
                env=env,
                timeout=15,
            )
            commits.append(git_output(repository, "rev-parse", "HEAD"))
            trees.append(git_output(repository, "rev-parse", "HEAD^{tree}"))
        return base_revision, base_tree, tuple(commits), tuple(trees)

    base_a, tree_a, commits_a, trees_a = create_chain(tmp_path / "chain-a", 2021)
    base_b, tree_b, commits_b, trees_b = create_chain(tmp_path / "chain-b", 2022)
    base_c, tree_c, commits_c, trees_c = create_chain(tmp_path / "chain-altered", 2023, altered_patch_index=5)
    assert base_a == base_b
    assert tree_a == tree_b
    assert base_a == base_c
    assert tree_a == tree_c
    assert commits_a != commits_b
    assert trees_a == trees_b
    assert trees_a != trees_c

    monkeypatch.setattr(module, "_X200_GRUB_BASE_REVISION", base_a)
    monkeypatch.setattr(module, "_X200_GRUB_BASE_TREE", tree_a)
    monkeypatch.setattr(module, "_X200_GRUB_PATCH_TREE_IDS", trees_a)
    monkeypatch.setattr(module, "_X200_GRUB_PATCHED_TREE", trees_a[-1])
    engine = ArtifactEngine()
    assert engine._read_x200_grub_patch_history(tmp_path / "chain-a") == (commits_a, trees_a)
    assert engine._read_x200_grub_patch_history(tmp_path / "chain-b") == (commits_b, trees_b)
    with pytest.raises(ValueError, match="per-patch source tree"):
        engine._read_x200_grub_patch_history(tmp_path / "chain-altered")
    base_evidence = X200GrubFontEvidence(
        source_font_path=module._X200_GRUB_FONT_SOURCE_PATH,
        source_font_sha256=module._X200_GRUB_FONT_SOURCE_SHA256,
        source_package=module._X200_GRUB_FONT_PACKAGE,
        source_package_version=module._X200_GRUB_FONT_PACKAGE_VERSION,
        staged_font_relative_path=module._X200_GRUB_FONT_STAGED_RELATIVE_PATH,
        staged_font_sha256=module._X200_GRUB_FONT_SOURCE_SHA256,
        overlay_resource_path=module._X200_GRUB_FONT_OVERLAY_PATH,
        overlay_resource_sha256=module._X200_GRUB_FONT_OVERLAY_SHA256,
        lbmk_tree_sh_sha256=module._X200_LBMK_TREE_SH_SHA256,
        lbmk_tree_sh_patched_sha256=module._X200_LBMK_TREE_SH_PATCHED_SHA256,
        grub_source_relative_path="work/lbmk/src/grub/default",
        grub_repository_url=module._X200_GRUB_SOURCE_URL,
        grub_cache_relative_path=module._X200_GRUB_CACHE_RELATIVE_PATH,
        grub_base_revision=base_a,
        grub_base_tree_id=tree_a,
        grub_patch_inputs=module._X200_GRUB_PATCH_INPUTS,
        grub_patch_commits=commits_a,
        grub_patch_tree_ids=trees_a,
        grub_result_revision=commits_a[-1],
        grub_result_tree_id=trees_a[-1],
        configure_log_relative_path=module._X200_GRUB_CONFIG_LOG_RELATIVE_PATH,
        configure_log_sha256="a" * 64,
        configure_argv=(
            "./configure",
            "--with-unifont=<operation-output-directory>/inputs/unifont.otf",
            *module._X200_GRUB_CONFIGURE_FIXED_ARGS,
        ),
        generated_payload_relative_path=module._X200_GRUB_PAYLOAD_RELATIVE_PATH,
        generated_payload_sha256="b" * 64,
    )
    alternate_evidence = replace(
        base_evidence,
        grub_patch_commits=commits_b,
        grub_result_revision=commits_b[-1],
    )
    assert base_evidence.grub_patch_tree_ids == alternate_evidence.grub_patch_tree_ids
    assert base_evidence.grub_result_revision != alternate_evidence.grub_result_revision
    with pytest.raises(ValueError, match="pinned lbmk/GRUB recipe"):
        replace(base_evidence, grub_patch_tree_ids=("f" * 40, *trees_a[1:]))


def test_x200_grub_patch_input_files_are_rechecked_by_exact_name_and_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autoloader.rom_builder.artifact_engine as module

    contents = {"0001-test.patch": b"fixture patch one\n", "0002-test.patch": b"fixture patch two\n"}
    pins = tuple((name, hashlib.sha256(data).hexdigest()) for name, data in contents.items())
    monkeypatch.setattr(module, "_X200_GRUB_PATCH_INPUTS", pins)
    root = tmp_path / "lbmk"
    patch_dir = root / "config" / "grub" / "default" / "patches"
    patch_dir.mkdir(parents=True)
    for name, data in contents.items():
        (patch_dir / name).write_bytes(data)
    ArtifactEngine._verify_x200_grub_patch_inputs(root)

    (patch_dir / "0002-test.patch").write_bytes(b"altered patch content\n")
    with pytest.raises(ValueError, match="patch input changed"):
        ArtifactEngine._verify_x200_grub_patch_inputs(root)


def test_prepare_x200_grub_font_stages_exact_input_and_applies_reviewed_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autoloader.rom_builder.artifact_engine as module

    font_bytes = b"Synthetic OTF fixture; this is not a font and no build is run.\n"
    font_hash = hashlib.sha256(font_bytes).hexdigest()
    source_font = tmp_path / "system" / "unifont.otf"
    source_font.parent.mkdir()
    source_font.write_bytes(font_bytes)
    monkeypatch.setattr(module, "_X200_GRUB_FONT_SOURCE_PATH", str(source_font.resolve()))
    monkeypatch.setattr(module, "_X200_GRUB_FONT_SOURCE_SHA256", font_hash)

    original_tree = b"synthetic pinned include/tree.sh before overlay\n"
    patched_tree = b"synthetic pinned include/tree.sh after overlay\n"
    monkeypatch.setattr(module, "_X200_LBMK_TREE_SH_SHA256", hashlib.sha256(original_tree).hexdigest())
    monkeypatch.setattr(module, "_X200_LBMK_TREE_SH_PATCHED_SHA256", hashlib.sha256(patched_tree).hexdigest())

    operation = tmp_path / "operation"
    source = operation / "work" / "lbmk"
    tree_script = source / "include" / "tree.sh"
    tree_script.parent.mkdir(parents=True)
    tree_script.write_bytes(original_tree)
    patch_contents = tuple(
        (name, f"synthetic pinned fixture patch {index}\n".encode("ascii"))
        for index, (name, _digest) in enumerate(module._X200_GRUB_PATCH_INPUTS)
    )
    fixture_patch_inputs = tuple((name, hashlib.sha256(data).hexdigest()) for name, data in patch_contents)
    monkeypatch.setattr(module, "_X200_GRUB_PATCH_INPUTS", fixture_patch_inputs)
    patch_directory = source / "config" / "grub" / "default" / "patches"
    patch_directory.mkdir(parents=True)
    for patch_name, patch_data in patch_contents:
        (patch_directory / patch_name).write_bytes(patch_data)
    logs = operation / "logs"
    logs.mkdir(parents=True)
    commands_seen: list[tuple[str, ...]] = []

    def fake_readonly(argv, _cwd, _logs, _commands):
        command = tuple(argv)
        commands_seen.append(command)
        if command[:2] == ("dpkg-query", "-S"):
            return f"{module._X200_GRUB_FONT_PACKAGE}: {source_font}\n"
        if command[:2] == ("dpkg-query", "-W"):
            return f"{module._X200_GRUB_FONT_PACKAGE}\t{module._X200_GRUB_FONT_PACKAGE_VERSION}\n"
        if command[:3] == ("git", "-C", str(source)):
            return " M include/tree.sh\n"
        raise AssertionError(f"Unexpected read-only command: {command!r}")

    def fake_logged(argv, _cwd, _logs, _commands):
        command = tuple(argv)
        commands_seen.append(command)
        if command[:3] == ("git", "-C", str(source)) and command[3:5] == ("apply", "--check"):
            return None
        if command[:3] == ("git", "-C", str(source)) and command[3] == "apply":
            tree_script.write_bytes(patched_tree)
            return None
        raise AssertionError(f"Unexpected logged command: {command!r}")

    engine = ArtifactEngine()
    monkeypatch.setattr(engine, "_run_readonly", fake_readonly)
    monkeypatch.setattr(engine, "_run_logged", fake_logged)
    staged = engine._prepare_x200_grub_font(source, operation, logs, [])

    assert staged == operation / "inputs" / "unifont.otf"
    assert staged.read_bytes() == font_bytes
    assert hashlib.sha256(staged.read_bytes()).hexdigest() == font_hash
    assert hashlib.sha256(tree_script.read_bytes()).hexdigest() == module._X200_LBMK_TREE_SH_PATCHED_SHA256
    assert any(command[3:5] == ("apply", "--check") for command in commands_seen if command[:2] == ("git", "-C"))
    assert any(command[3] == "apply" for command in commands_seen if command[:2] == ("git", "-C"))


def test_x200_unifont_source_hash_mismatch_blocks_before_package_or_git_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autoloader.rom_builder.artifact_engine as module

    source_font = tmp_path / "unifont.otf"
    source_font.write_bytes(b"unexpected source bytes")
    monkeypatch.setattr(module, "_X200_GRUB_FONT_SOURCE_PATH", str(source_font.resolve()))
    monkeypatch.setattr(module, "_X200_GRUB_FONT_SOURCE_SHA256", "0" * 64)
    operation = tmp_path / "operation"
    source = operation / "work" / "lbmk"
    (source / "include").mkdir(parents=True)
    (source / "include" / "tree.sh").write_bytes(b"not reached")
    logs = operation / "logs"
    logs.mkdir(parents=True)
    invoked = []
    engine = ArtifactEngine()
    monkeypatch.setattr(engine, "_run_readonly", lambda *args, **kwargs: invoked.append(args) or "")
    monkeypatch.setattr(engine, "_run_logged", lambda *args, **kwargs: invoked.append(args))
    with pytest.raises(ValueError, match="source bytes differ from the pinned OTF"):
        engine._prepare_x200_grub_font(source, operation, logs, [])
    assert invoked == []
    assert not (operation / "inputs").exists()


@pytest.mark.parametrize("bad_field", ["owner", "version"])
def test_x200_unifont_package_identity_mismatch_blocks_staging(
    bad_field: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autoloader.rom_builder.artifact_engine as module

    font = tmp_path / "unifont.otf"
    font.write_bytes(b"synthetic font bytes")
    logs = tmp_path / "logs"
    logs.mkdir()
    seen = []

    def fake_readonly(argv, _cwd, _logs, _commands):
        command = tuple(argv)
        seen.append(command)
        if command[:2] == ("dpkg-query", "-S"):
            package = "other-package" if bad_field == "owner" else module._X200_GRUB_FONT_PACKAGE
            return f"{package}: {font}\n"
        if command[:2] == ("dpkg-query", "-W"):
            version = "1:0-1" if bad_field == "version" else module._X200_GRUB_FONT_PACKAGE_VERSION
            return f"{module._X200_GRUB_FONT_PACKAGE}\t{version}\n"
        raise AssertionError(f"Unexpected package query: {command!r}")

    engine = ArtifactEngine()
    monkeypatch.setattr(engine, "_run_readonly", fake_readonly)
    with pytest.raises(ValueError, match="expected Debian fonts-unifont package" if bad_field == "owner" else "package version"):
        engine._verify_x200_unifont_package(font, logs, [])
    assert len(seen) == 2


def test_x200_build_command_receives_only_operation_staged_unifont(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autoloader.rom_builder.artifact_engine as module

    font_bytes = b"Synthetic OTF fixture; no GRUB build is run.\n"
    monkeypatch.setattr(module, "_X200_GRUB_FONT_SOURCE_SHA256", hashlib.sha256(font_bytes).hexdigest())
    operation = tmp_path / "operation"
    logs = operation / "logs"
    logs.mkdir(parents=True)
    build_source = operation / "work" / "lbmk"
    build_source.mkdir(parents=True)
    font = operation / "inputs" / "unifont.otf"
    font.parent.mkdir()
    font.write_bytes(font_bytes)
    _ensure_operation_git_config(operation)
    observed: list[dict[str, str]] = []

    def fake_run(argv, **kwargs):
        observed.append(kwargs["env"])
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    ArtifactEngine()._run_logged(
        X200_4MB.build_command,
        build_source,
        logs,
        [],
        x200_unifont_path=font,
    )
    assert observed[0]["LIBRECORE_UNIFONT_PATH"] == str(font)

    with pytest.raises(ValueError, match="exact staged X200 Unifont"):
        ArtifactEngine()._run_logged(
            ("./mk", "-b", "coreboot", "x220"),
            build_source,
            logs,
            [],
            x200_unifont_path=font,
        )
    assert len(observed) == 1
    assert X200_8MB.expected_config_sha256 == "6435d325d4f77c18ec6b411d708508d4eda2e5a4d2f6b83dfd3c0c0d230efac4"
    assert tuple(item.sha256 for item in ONEVYRAIN_HANDOFF.source.files) == (
        "5af0f463e56e8fcfbc6d3b57224859e755e701d4e04087167c9e335b14e8c33a",
        "d00089253cb56a8967e242a9e73496c413a7c3d378044968acbc6278b31e873a",
    )


def test_x200_uboot_overlay_is_pinned_and_changes_only_the_x86_64_objcopy_target(tmp_path: Path) -> None:
    import autoloader.rom_builder.artifact_engine as module

    patch_path = Path(module.__file__).parent / "patches" / module._X200_UBOOT_OVERLAY_INPUT[0]
    assert hashlib.sha256(patch_path.read_bytes()).hexdigest() == module._X200_UBOOT_OVERLAY_SHA256
    assert module._UBOOT_X86_64_BASE_REVISION == "f919c3a889f0ec7d63a48b5d0ed064386b0980bd"
    assert module._UBOOT_X86_64_BASE_TREE == "66e7e696eeaa875250ff12d586db009bf8631056"
    assert module._UBOOT_X86_64_PATCHED_TREE == "12e245c8ad4d68099c7127afd5c61a0e36992b1c"
    assert tuple(name for name, _ in module._X200_UBOOT_PATCH_STACK) == tuple(sorted(name for name, _ in module._X200_UBOOT_PATCH_STACK))

    repository = tmp_path / "u-boot-fixture"
    config_path = repository / "arch" / "x86" / "config.mk"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "header\n" * 128
        + "ifdef CONFIG_X86_64\n"
        + "EFI_TARGET := --target=efi-app-x86_64\n"
        + "else\nEFI_TARGET := --target=efi-app-ia32\nendif\n",
        encoding="utf-8",
        newline="\n",
    )

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], cwd=repository, check=True, capture_output=True, text=True)

    git("init", "--quiet")
    git("config", "user.name", "Synthetic Patch Test")
    git("config", "user.email", "test@example.invalid")
    git("add", "arch/x86/config.mk")
    git("commit", "--quiet", "-m", "fixture base")
    base = git("rev-parse", "HEAD").stdout.strip()
    git("am", str(patch_path))
    applied_commit = git("rev-parse", "HEAD").stdout.strip()
    changed = config_path.read_text(encoding="utf-8")
    assert changed.count("EFI_TARGET := --output-target=efi-app-x86_64") == 1
    assert changed.count("EFI_TARGET := --target=efi-app-ia32") == 1
    assert "EFI_TARGET := --target=efi-app-x86_64" not in changed
    diff = git("diff", "--no-ext-diff", "--unified=0", f"{base}..HEAD", "--", "arch/x86/config.mk").stdout
    assert diff.count("-EFI_TARGET := --target=efi-app-x86_64") == 1
    assert diff.count("+EFI_TARGET := --output-target=efi-app-x86_64") == 1
    assert "--target=efi-app-ia32" not in diff
    engine = ArtifactEngine()
    operation_dir = tmp_path / "patch-check-operation"
    logs_dir = operation_dir / "logs"
    logs_dir.mkdir(parents=True)
    _ensure_operation_git_config(operation_dir)
    command_records: list[CommandRecord] = []
    assert engine._patch_file_produces_commit_tree(
        repository, base, applied_commit, patch_path, logs_dir, command_records
    )
    assert len(command_records) == 5
    assert all(record.exit_code == 0 for record in command_records)

    altered_patch = tmp_path / "same-subject-altered-content.patch"
    altered_bytes = patch_path.read_bytes().replace(
        b"EFI_TARGET := --output-target=efi-app-x86_64",
        b"EFI_TARGET := --output-target=efi-app-x86_64-altered",
    )
    altered_patch.write_bytes(altered_bytes)
    assert module._email_patch_subject(altered_bytes) == module._email_patch_subject(patch_path.read_bytes())
    assert not engine._patch_file_produces_commit_tree(
        repository, base, applied_commit, altered_patch, logs_dir, command_records
    )
    assert command_records[-1].exit_code != 0 or "write-tree" in command_records[-1].argv


def test_x200_uboot_cache_origin_binds_real_lbmk_local_cache_layout(tmp_path: Path) -> None:
    import autoloader.rom_builder.artifact_engine as module

    root = tmp_path / "disposable-lbmk"
    relative = "cache/clone/source.denx.de/u-boot/u-boot.git"
    cache = root / Path(*relative.split("/"))
    cache.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet"], cwd=cache, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", module._UBOOT_X86_64_SOURCE_URL],
        cwd=cache,
        check=True,
    )
    cache_remote = subprocess.run(
        ["git", "-C", str(cache), "remote", "get-url", "origin"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    # lbmk's generated src/u-boot worktree uses the local cache directory as
    # origin, while the cache's own origin remains the pinned HTTPS remote.
    worktree_origin = str(cache)
    cache_toplevel = str(cache.resolve())
    cache_git_dir = str((cache / ".git").resolve())
    assert module._validate_x200_uboot_cache_binding(
        root,
        worktree_origin,
        cache_toplevel,
        cache_git_dir,
        cache_remote,
        module._UBOOT_X86_64_BASE_REVISION,
        module._UBOOT_X86_64_BASE_TREE,
    ) == relative
    with pytest.raises(ValueError, match="remote differs"):
        module._validate_x200_uboot_cache_binding(
            root,
            worktree_origin,
            cache_toplevel,
            cache_git_dir,
            module._UBOOT_X86_64_BACKUP_URL,
            module._UBOOT_X86_64_BASE_REVISION,
            module._UBOOT_X86_64_BASE_TREE,
        )
    with pytest.raises(ValueError, match="exact pinned in-root cache paths"):
        module._validate_x200_uboot_cache_binding(
            root,
            str(tmp_path / "outside-cache.git"),
            cache_toplevel,
            cache_git_dir,
            module._UBOOT_X86_64_SOURCE_URL,
            module._UBOOT_X86_64_BASE_REVISION,
            module._UBOOT_X86_64_BASE_TREE,
        )
    with pytest.raises(ValueError, match="exact pinned in-root cache paths"):
        module._validate_x200_uboot_cache_binding(
            root,
            str(root / "cache" / "clone" / "untrusted.example" / "u-boot.git"),
            cache_toplevel,
            cache_git_dir,
            cache_remote,
            module._UBOOT_X86_64_BASE_REVISION,
            module._UBOOT_X86_64_BASE_TREE,
        )
    with pytest.raises(ValueError, match="exact pinned base"):
        module._validate_x200_uboot_cache_binding(
            root,
            worktree_origin,
            cache_toplevel,
            cache_git_dir,
            module._UBOOT_X86_64_SOURCE_URL,
            module._UBOOT_X86_64_BASE_REVISION,
            "0" * 40,
        )
    linked_root = tmp_path / "linked-lbmk"
    linked_root.mkdir()
    linked_cache_parent = linked_root / "cache" / "clone" / "source.denx.de" / "u-boot"
    linked_cache_parent.mkdir(parents=True)
    try:
        linked_cache_parent.joinpath("u-boot.git").symlink_to(cache, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable for this test host: {exc}")
    with pytest.raises(ValueError, match="symlink component"):
        module._validate_x200_uboot_cache_binding(
            linked_root,
            str(linked_cache_parent / "u-boot.git"),
            cache_toplevel,
            cache_git_dir,
            cache_remote,
            module._UBOOT_X86_64_BASE_REVISION,
            module._UBOOT_X86_64_BASE_TREE,
        )


def test_coreboot_2609_source_hashes_match_reviewed_raw_blobs() -> None:
    expected = {
        "configs/builder/config.lenovo_x220": "beeb15673bd4ef1394e19c7bbbac7f9ab3589552671d9502de4621b0b32c2cd1",
        "src/mainboard/lenovo/x220/Kconfig.name": "1ca8ac6f14193e04be8d49e2ddfc7383aed8af0988db93d482140b6852664fa5",
        "src/mainboard/lenovo/x220/Kconfig": "8c94a9d6976698b6e5cd7e3c3e0ee652a9916a878e85ab6bb9f7ba85a6c71f53",
        "src/Kconfig": "0557dc3ea3ffbdeaddd9c55dd8fbfe0e6d8259b18e7ca250f403fd5fe992eb01",
        "util/cbfstool/cbfs_image.c": "fd6a56a8e20c15f4fd8f03511f05ce8264f120ad0a0dc11900e2e04f89a88dda",
        "util/cbfstool/cbfstool.c": "f34621b9d58083fc7c3e31571aaf5bd02f78476101e249a53995189d8a71488b",
    }
    assert {item.path: item.sha256 for item in COREBOOT_2609.files} == expected
    assert X220_COREBOOT.expected_config_sha256 == expected["configs/builder/config.lenovo_x220"]
    assert COREBOOT_2609.repository_url == "https://github.com/coreboot/coreboot.git"


def test_x220_inspect_source_accepts_only_the_documented_github_mirror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autoloader.rom_builder.artifact_engine as module

    source = tmp_path / "coreboot"
    expected_hashes = {item.path: item.sha256 for item in COREBOOT_2609.files}
    for relative_path in expected_hashes:
        candidate = source / Path(*relative_path.split("/"))
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(b"raw pinned fixture bytes")

    engine = ArtifactEngine()
    git_values = {
        ("remote", "get-url", "origin"): COREBOOT_2609.repository_url,
        ("rev-parse", "HEAD"): COREBOOT_2609.revision,
        ("rev-parse", "HEAD^{tree}"): COREBOOT_2609.tree_id,
        ("status", "--porcelain=v1", "--untracked-files=all"): "",
    }
    monkeypatch.setattr(engine, "_git_value", lambda _source, *args: git_values[args])

    def pinned_fixture_hash(path: Path) -> str:
        relative = path.relative_to(source).as_posix()
        return expected_hashes[relative]

    monkeypatch.setattr(module, "sha256_file", pinned_fixture_hash)

    audit = engine.inspect_source(X220_COREBOOT.recipe_id, source)
    assert audit.clean is True
    assert audit.repository_url == "https://github.com/coreboot/coreboot.git"
    assert audit.revision == COREBOOT_2609.revision
    assert audit.tree_id == COREBOOT_2609.tree_id
    assert dict(audit.pinned_file_hashes) == expected_hashes

    for rejected_origin in (
        "https://review.coreboot.org/coreboot.git",
        "https://github.com/untrusted/coreboot.git",
    ):
        git_values[("remote", "get-url", "origin")] = rejected_origin
        with pytest.raises(ValueError, match="origin"):
            engine.inspect_source(X220_COREBOOT.recipe_id, source)


_CBFS_FIXTURE = "\n".join((
    "Name\tOffset\tType\tMetadata Size\tData Size\tTotal Size",
    "fallback/romstage\t0x0\tstage\t0x20\t0x1000\t0x1020",
    "fallback/payload\t0x2000\tpayload\t0x20\t0x4000\t0x4020",
    "(empty)\t0x7000\tnull\t0x20\t0x1000\t0x1020",
))
_X200_CBFS_FIXTURE = _CBFS_FIXTURE.replace("\tpayload\t", "\tsimple elf\t")


def test_cbfs_parseable_inventory_requires_pinned_header_bounds_and_payload() -> None:
    inventory = parse_cbfs_parseable_output(
        _CBFS_FIXTURE,
        required_payload="fallback/payload",
        region_size_bytes=0x9000,
    )
    assert tuple(item.name for item in inventory) == (
        "fallback/romstage", "fallback/payload", "(empty)"
    )
    assert inventory[1].entry_type == "payload"
    assert inventory[1].total_size_bytes == 0x4020

    invalid = (
        _CBFS_FIXTURE.replace("\t0x4020\n", "\t0x4021\n"),
        _CBFS_FIXTURE.replace("\tpayload\t", "\traw\t"),
        _CBFS_FIXTURE.replace("fallback/payload", "fallback/romstage"),
        _CBFS_FIXTURE.replace("0x7000", "0x9000"),
        _CBFS_FIXTURE.replace("\t0x1000\t0x1020\n", "\t0x1000\n"),
        _CBFS_FIXTURE.replace("\tnull\t", "\t\t"),
        _CBFS_FIXTURE + "\nfallback/romstage\t0x8000\traw\t0x20\t0x100\t0x120",
    )
    for output in invalid:
        with pytest.raises(ValueError):
            parse_cbfs_parseable_output(
                output,
                required_payload="fallback/payload",
                region_size_bytes=0x9000,
            )


def test_cbfs_parseable_inventory_allows_adjacency_and_repeated_null_placeholders() -> None:
    adjacent = "\n".join((
        "Name\tOffset\tType\tMetadata Size\tData Size\tTotal Size",
        "fallback/romstage\t0x0\tstage\t0x20\t0x1000\t0x1020",
        "fallback/payload\t0x1020\tpayload\t0x20\t0x4000\t0x4020",
        "(empty)\t0x5040\tnull\t0x20\t0x1000\t0x1020",
        "(empty)\t0x6060\tnull\t0x20\t0x100\t0x120",
    ))
    inventory = parse_cbfs_parseable_output(
        adjacent,
        required_payload="fallback/payload",
        region_size_bytes=0x9000,
    )
    assert [entry.name for entry in inventory].count("(empty)") == 2
    mixed_null_types = adjacent + "\n(empty)\t0x6180\traw\t0x20\t0x100\t0x120"
    with pytest.raises(ValueError, match="empty placeholder.*null type"):
        parse_cbfs_parseable_output(
            mixed_null_types,
            required_payload="fallback/payload",
            region_size_bytes=0x9000,
        )


def test_cbfs_parseable_inventory_rejects_overlapping_entry_intervals() -> None:
    overlapping = _CBFS_FIXTURE.replace("fallback/payload\t0x2000", "fallback/payload\t0x1000")
    with pytest.raises(ValueError, match="CBFS rows .* overlap"):
        parse_cbfs_parseable_output(
            overlapping,
            required_payload="fallback/payload",
            region_size_bytes=0x9000,
        )


def test_cbfs_parseable_inventory_rejects_null_intervals_overlapping_payload_or_null() -> None:
    null_overlaps_payload = _CBFS_FIXTURE.replace("(empty)\t0x7000", "(empty)\t0x3000")
    null_overlaps_null = _CBFS_FIXTURE + "\n(empty)\t0x7800\tnull\t0x20\t0x1000\t0x1020"
    for output in (null_overlaps_payload, null_overlaps_null):
        with pytest.raises(ValueError, match="CBFS rows .* overlap"):
            parse_cbfs_parseable_output(
                output,
                required_payload="fallback/payload",
                region_size_bytes=0x9000,
            )


def test_cbfs_parseable_inventory_separates_x200_and_x220_payload_types() -> None:
    x200 = parse_cbfs_parseable_output(
        _X200_CBFS_FIXTURE,
        required_payload="fallback/payload",
        required_payload_type="simple elf",
        region_size_bytes=0x9000,
    )
    assert next(entry for entry in x200 if entry.name == "fallback/payload").entry_type == "simple elf"

    with pytest.raises(ValueError, match="type 'simple elf'"):
        parse_cbfs_parseable_output(
            _CBFS_FIXTURE,
            required_payload="fallback/payload",
            required_payload_type="simple elf",
            region_size_bytes=0x9000,
        )
    x220 = parse_cbfs_parseable_output(
        _CBFS_FIXTURE,
        required_payload="fallback/payload",
        region_size_bytes=0x9000,
    )
    assert next(entry for entry in x220 if entry.name == "fallback/payload").entry_type == "payload"


def test_cbfs_parseable_inventory_accepts_pinned_nonverbose_region_output() -> None:
    # Pinned cbfstool `print -r COREBOOT -k` has the header directly; unlike
    # verbose output it does not include an FMAP marker.
    inventory = parse_cbfs_parseable_output(
        _CBFS_FIXTURE,
        required_payload="fallback/payload",
        region_size_bytes=0x9000,
        expected_region="COREBOOT",
    )
    assert inventory[1].name == "fallback/payload"

    for output, expected_region in (
        ("[FMAP REGION]\tBIOS\n" + _CBFS_FIXTURE, "COREBOOT"),
        ("[FMAP REGION]\tCOREBOOT\n" + _CBFS_FIXTURE, None),
    ):
        with pytest.raises(ValueError):
            parse_cbfs_parseable_output(
                output,
                required_payload="fallback/payload",
                region_size_bytes=0x9000,
                expected_region=expected_region,
            )


def test_cbfs_inspection_records_exact_pinned_tool_invocations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import autoloader.rom_builder.artifact_engine as module

    engine = ArtifactEngine()
    source = tmp_path / "source"
    coreboot_tool = source / "util" / "cbfstool" / "cbfstool"
    coreboot_tool.parent.mkdir(parents=True)
    coreboot_tool.write_bytes(b"fake coreboot cbfstool")
    rom = tmp_path / "coreboot.rom"
    rom.write_bytes(b"fake rom")
    logs = tmp_path / "logs"
    logs.mkdir()
    invocations: list[tuple[str, ...]] = []

    def fake_readonly(argv, _cwd, _logs, commands):
        invocations.append(tuple(argv))
        if "-r" in argv:
            return _CBFS_FIXTURE
        return _X200_CBFS_FIXTURE

    monkeypatch.setattr(engine, "_run_readonly", fake_readonly)
    records: list[CommandRecord] = []
    record = engine._inspect_cbfs(X220_COREBOOT, source, rom, logs, records)
    assert invocations == [(str(coreboot_tool), str(rom), "print", "-r", "COREBOOT", "-k")]
    assert isinstance(record, CBFSInspectionRecord)
    assert record.source_revision == COREBOOT_2609.revision
    assert record.region == "COREBOOT"
    assert record.tool_sha256 == hashlib.sha256(coreboot_tool.read_bytes()).hexdigest()
    assert record.source_file_hashes == (
        ("util/cbfstool/cbfs_image.c", "fd6a56a8e20c15f4fd8f03511f05ce8264f120ad0a0dc11900e2e04f89a88dda"),
        ("util/cbfstool/cbfstool.c", "f34621b9d58083fc7c3e31571aaf5bd02f78476101e249a53995189d8a71488b"),
    )

    lbmk_recipe = X200_4MB
    lbmk_tool = source / "elf" / "coreboot" / "default" / "cbfstool"
    lbmk_tool.parent.mkdir(parents=True)
    lbmk_tool.write_bytes(b"fake pinned nested cbfstool")
    monkeypatch.setattr(
        engine,
        "_validate_lbmk_nested_coreboot",
        lambda _path, _logs, _commands: _synthetic_x200_nested_repository_evidence(),
    )
    invocations.clear()
    engine._inspect_cbfs(lbmk_recipe, source, rom, logs, records)
    assert invocations == [(str(lbmk_tool), str(rom), "print", "-k")]


def _make_x220_backup_fixture(tmp_path: Path):
    from autoloader.rom_builder.artifact_engine import BlobInput

    mib = 1024 * 1024
    size = 8 * mib
    original = (bytes(range(251)) * ((size // 251) + 1))[:size]
    regions = (
        RegionEvidence("ifd", 0, 0x1000),
        RegionEvidence("gbe", 0x1000, 0x3000),
        RegionEvidence("me", 0x3000, 0x500000),
        RegionEvidence("bios", 0x500000, size),
    )
    intent = BuildIntentEvidence(
        profile_id="x220-coreboot",
        requested_model_id="x220",
        firmware_family=FirmwareFamily.COREBOOT,
        device=DeviceEvidence(
            raw_dmi_product_name="ThinkPad X220",
            raw_dmi_product_version="4291CTO",
            raw_dmi_system_family="ThinkPad X220",
            raw_mtm="4291CTO",
            raw_bios_version="G1ET99WW (9.99)",
        ),
        topology=ChipTopology(
            observed=True,
            chips=(ChipEvidence(
                "main", size, True, physical_label="main", voltage_mv=3300,
                voltage_evidence_source="synthetic-test-fixture",
                voltage_attested_by="fixture-operator",
                voltage_attested_at="2026-10-10T00:00:00+00:00",
                voltage_instrument_id="fixture-meter",
                voltage_test_point="fixture-vcc",
            ),),
            regions=regions,
        ),
        programmer=ProgrammerEvidence(
            kind=ProgrammerKind.EXTERNAL_SPI,
            observed=True,
            interface_verified=True,
            wiring_verified=True,
            voltage_mv=3300,
            evidence_source="synthetic-test-fixture",
            attested_by="fixture-operator",
            attested_at="2026-10-10T00:00:00+00:00",
            instrument_id="fixture-meter",
            measurement_test_point="fixture-vcc",
        ),
        recipe_id=X220_COREBOOT.recipe_id,
        source_url=COREBOOT_2609.repository_url,
        source_revision=COREBOOT_2609.revision,
        source_tree_id=COREBOOT_2609.tree_id,
        configuration_sha256=X220_COREBOOT.expected_config_sha256,
        expected_size_bytes=size,
    )
    primary_dir, recovery_dir = tmp_path / "primary", tmp_path / "recovery"
    primary_dir.mkdir()
    recovery_dir.mkdir()
    primary_path, recovery_path = primary_dir / "chip-main.rom", recovery_dir / "chip-main.rom"
    primary_path.write_bytes(original)
    recovery_path.write_bytes(original)
    digest = hashlib.sha256(original).hexdigest()
    captured = CapturedChipBackup(
        chip_id="main",
        observed_chip_name="W25Q64.V",
        capacity_bytes=size,
        first_path=str(primary_path),
        second_path=str(recovery_path),
        first_sha256=digest,
        second_sha256=digest,
        first_capture_id="capture-original-1",
        second_capture_id="capture-original-2",
    )
    pair = BackupPairEvidence(
        chip_id="main",
        first_path=str(primary_path),
        second_path=str(recovery_path),
        first_sha256=digest,
        second_sha256=digest,
        first_capture_id=captured.first_capture_id,
        second_capture_id=captured.second_capture_id,
        transient=False,
    )
    intent = replace(intent, backup=BackupEvidence((pair,)))
    backup_operation_id = str(uuid.uuid4())
    event = {
        "operation_id": backup_operation_id,
        "status": "verified",
        "profile_id": intent.profile_id,
        "requested_model_id": intent.requested_model_id,
        "programmer": "ch341a_spi",
        "tool": {
            "name": "flashrom",
            "executable": "fixture-flashrom",
            "version": "fixture-tool",
        },
        "device_identifiers": {
            "raw_dmi_product_name": intent.device.raw_dmi_product_name,
            "raw_mtm": intent.device.raw_mtm,
            "raw_bios_version": intent.device.raw_bios_version,
        },
        "source_artifact": None,
        "build_intent": build_intent_record(intent),
        "build_intent_sha256": build_intent_sha256(intent),
        "topology": [{
            "chip_id": "main",
            "physical_label": "main",
            "capacity_bytes": size,
            "voltage_mv": 3300,
            "observed_chip_name": "W25Q64.V",
        }],
        "region_map": [
            {"name": region.name, "start_byte": region.start_byte, "end_byte": region.end_byte}
            for region in regions
        ],
        "regions": [
            {"name": region.name, "start_byte": region.start_byte, "end_byte": region.end_byte}
            for region in regions
        ],
        "primary_storage": {
            "path": str(primary_dir),
            "volume_id": "primary-volume",
            "filesystem_type": "ext4",
            "available_bytes": 2 * size,
            "required_bytes": size,
            "persistent_confirmed": True,
            "scratch_readback_verified": True,
            "observed_at_utc": "2026-10-10T00:00:00+00:00",
        },
        "recovery_storage": {
            "path": str(recovery_dir),
            "volume_id": "recovery-volume",
            "filesystem_type": "ext4",
            "available_bytes": 2 * size,
            "required_bytes": size,
            "persistent_confirmed": True,
            "scratch_readback_verified": True,
            "observed_at_utc": "2026-10-10T00:00:00+00:00",
        },
        "chips": [{
            "chip_id": "main",
            "capacity_bytes": size,
            "first_path": str(primary_path),
            "second_path": str(recovery_path),
            "first_size_bytes": size,
            "second_size_bytes": size,
            "first_sha256": digest,
            "second_sha256": digest,
            "first_capture_id": captured.first_capture_id,
            "second_capture_id": captured.second_capture_id,
        }],
    }
    primary_manifest_hash = BackupManifestStore(primary_dir).append(event)
    recovery_manifest_hash = BackupManifestStore(recovery_dir).append(event)

    def storage(path: Path, volume: str) -> StorageEvidence:
        return StorageEvidence(
            path=str(path),
            volume_id=volume,
            filesystem_type="ext4",
            available_bytes=2 * size,
            required_bytes=size,
            persistence_confirmed=True,
            scratch_readback_verified=True,
            observed_at_utc="2026-10-10T00:00:00+00:00",
        )

    backup = BackupSetResult(
        operation_id=backup_operation_id,
        profile_id=intent.profile_id,
        created_at_utc="2026-10-10T00:00:00+00:00",
        tool_name="flashrom",
        tool_version="fixture-tool",
        programmer="ch341a_spi",
        backup=BackupEvidence((pair,)),
        chips=(captured,),
        primary_manifest=str(primary_dir / "backup-manifest.json"),
        recovery_manifest=str(recovery_dir / "backup-manifest.json"),
        primary_manifest_sha256=primary_manifest_hash,
        recovery_manifest_sha256=recovery_manifest_hash,
        primary_storage=storage(primary_dir, "primary-volume"),
        recovery_storage=storage(recovery_dir, "recovery-volume"),
    )
    blob_inputs = []
    for name, region_name in (("descriptor.bin", "ifd"), ("me.bin", "me"), ("gbe.bin", "gbe")):
        region = next(item for item in regions if item.name == region_name)
        data = original[region.start_byte:region.end_byte]
        path = tmp_path / name
        path.write_bytes(data)
        blob_inputs.append(BlobInput(name, path, hashlib.sha256(data).hexdigest()))
    return intent, backup, regions, tuple(blob_inputs), original


def test_x220_backup_evidence_binds_blobs_to_matching_full_chip_backup(tmp_path: Path) -> None:
    intent, backup, regions, blob_inputs, original = _make_x220_backup_fixture(tmp_path)
    observed, provenance = validate_x220_backup_evidence(intent, backup, regions, blob_inputs)
    assert observed == original
    assert provenance.operation_id == backup.operation_id
    assert provenance.original_chip_sha256 == backup.chips[0].first_sha256
    assert provenance.preserved_regions == ("gbe", "ifd", "me")


def test_fresh_x220_output_hash_is_bound_only_after_generation(tmp_path: Path) -> None:
    from autoloader.workflow.compatibility import CompatibilityValidator
    from autoloader.workflow.types import CompatibilityDecision

    intent, backup, regions, _blob_inputs, _original = _make_x220_backup_fixture(tmp_path)
    generated = tmp_path / "newly-generated-coreboot.rom"
    fresh_bytes = (bytes(range(239)) * ((X220_COREBOOT.expected_size_bytes // 239) + 1))[
        :X220_COREBOOT.expected_size_bytes
    ]
    generated.write_bytes(fresh_bytes)
    observed_digest = hashlib.sha256(fresh_bytes).hexdigest()

    final_plan = _finalize_x220_operation_plan(intent, backup, generated, regions)

    assert final_plan.artifact.expected_sha256 == observed_digest
    assert final_plan.artifact.path == str(generated.resolve())
    assert CompatibilityValidator().validate(final_plan).decision is CompatibilityDecision.ALLOWED
    with pytest.raises(ValueError, match="hash differs"):
        _finalize_x220_operation_plan(intent, backup, generated, regions, expected_sha256="0" * 64)

    generated.write_bytes(bytes((fresh_bytes[0] ^ 0xFF,)) + fresh_bytes[1:])
    result = CompatibilityValidator().validate(final_plan)
    assert result.decision is CompatibilityDecision.BLOCKED
    assert any("content does not match" in reason for reason in result.reasons)


def test_x220_backup_evidence_blocks_missing_or_unbound_inputs(tmp_path: Path) -> None:
    intent, backup, regions, blob_inputs, _original = _make_x220_backup_fixture(tmp_path)
    with pytest.raises(ValueError, match="complete original backup"):
        validate_x220_backup_evidence(intent, None, regions, blob_inputs)

    me_input = next(item for item in blob_inputs if item.name == "me.bin")
    me_input.path.write_bytes(b"incorrect ME blob")
    wrong_blob = replace(me_input, sha256=hashlib.sha256(me_input.path.read_bytes()).hexdigest())
    inputs = tuple(wrong_blob if item.name == me_input.name else item for item in blob_inputs)
    with pytest.raises(ValueError, match="exact matching slice"):
        validate_x220_backup_evidence(intent, backup, regions, inputs)


def test_x220_backup_evidence_rejects_changed_backup_or_wrong_region_map(tmp_path: Path) -> None:
    intent, backup, regions, blob_inputs, _original = _make_x220_backup_fixture(tmp_path)
    primary = Path(backup.chips[0].first_path)
    primary.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="exact observed chip capacity"):
        validate_x220_backup_evidence(intent, backup, regions, blob_inputs)

    second_root = tmp_path / "second"
    second_root.mkdir()
    intent, backup, regions, blob_inputs, _original = _make_x220_backup_fixture(second_root)
    changed = regions[:-1] + (RegionEvidence("bios", 0x510000, 8 * 1024 * 1024),)
    with pytest.raises(ValueError, match="differ from the evidence-bound"):
        validate_x220_backup_evidence(intent, backup, changed, blob_inputs)


def test_pinned_lbmk_output_name_is_derived_and_allowlisted() -> None:
    assert derive_lbmk_output_path("x200_4mb", "libgfxinit_corebootfb", "seabios") == (
        "bin/x200_4mb/seabios_x200_4mb_libgfxinit_corebootfb.rom"
    )
    assert derive_lbmk_output_path("x200_8mb", "libgfxinit_txtmode", "seabios") == (
        "bin/x200_8mb/seabios_x200_8mb_libgfxinit_txtmode.rom"
    )
    for args in (
        ("x200s_4mb", "libgfxinit_corebootfb", "seabios"),
        ("x200_4mb", "unknown", "seabios"),
        ("x200_4mb", "libgfxinit_corebootfb", "grub"),
    ):
        with pytest.raises(ValueError):
            derive_lbmk_output_path(*args)


def test_lbmk_output_inventory_matches_pinned_cprom_and_target_inputs(tmp_path: Path) -> None:
    assert any(item.path == "include/rom.sh" for item in LIBREBOOT_2601.files)
    assert {item.path for item in LIBREBOOT_2601.files} >= {
        "config/coreboot/x200_4mb/target.cfg",
        "config/coreboot/x200_4mb/config/libgfxinit_corebootfb",
        "config/coreboot/x200_4mb/config/libgfxinit_txtmode",
        "config/coreboot/x200_8mb/target.cfg",
        "config/coreboot/x200_8mb/config/libgfxinit_corebootfb",
        "config/coreboot/x200_8mb/config/libgfxinit_txtmode",
    }
    # The pinned include/rom.sh cprom() at lines 448-473 derives ROM names
    # from payload, target, init/display mode and .gkb keymap. Its
    # mkcorebootbin_real() iterates both target configs; target.cfg enables
    # SeaBIOS, GRUB, memtest and amd64 U-Boot. Therefore both target sizes
    # produce the same 31 distinct names, including one canonical SeaBIOS
    # corebootfb variant selected by the recipe.
    source = tmp_path / "pinned-lbmk"
    source.mkdir()
    outputs = _populate_lbmk_output_fixture(source, X200_4MB)
    relative_paths = tuple(path.relative_to(source).as_posix() for path in outputs)
    assert len(relative_paths) == 31
    assert X200_4MB.output_relative_path in relative_paths
    assert "bin/x200_4mb/seauboot_x200_4mb_libgfxinit_corebootfb.rom" in relative_paths
    assert "bin/x200_4mb/seagrub_x200_4mb_libgfxinit_txtmode_usqwerty.rom" in relative_paths
    assert not any("seauboot" in path and "txtmode" in path for path in relative_paths)


def test_kconfig_parser_rejects_duplicate_and_recipe_mismatch() -> None:
    assert parse_kconfig("CONFIG_BOARD_LENOVO_X220=y\n# CONFIG_PAYLOAD_NONE is not set\n") == {
        "BOARD_LENOVO_X220": "y",
        "PAYLOAD_NONE": "n",
    }
    with pytest.raises(ValueError, match="Duplicate"):
        parse_kconfig("CONFIG_ROM_SIZE=0x800000\nCONFIG_ROM_SIZE=0x800000\n")

    expected = "\n".join(f"{key}={value}" for key, value in X220_COREBOOT.config_expectations)
    digest = validate_recipe_config(X220_COREBOOT, expected)
    assert digest == hashlib.sha256(expected.encode()).hexdigest()
    bad = expected.replace("CONFIG_BOARD_LENOVO_X220=y", "CONFIG_BOARD_LENOVO_X220I=y")
    with pytest.raises(ValueError):
        validate_recipe_config(X220_COREBOOT, bad)


def test_lbmk_resolved_config_must_match_exact_target() -> None:
    recipe = X200_4MB
    valid = "\n".join(f"{key}={value}" for key, value in recipe.config_expectations)
    assert validate_recipe_config(recipe, valid)
    with pytest.raises(ValueError):
        validate_recipe_config(recipe, valid.replace("CONFIG_ROM_SIZE=0x400000", "CONFIG_ROM_SIZE=0x800000"))
    with pytest.raises(ValueError):
        validate_recipe_config(recipe, valid.replace("CONFIG_BOARD_LENOVO_X200=y", "CONFIG_BOARD_LENOVO_X200S=y"))


def test_x220_ifd_map_requires_exact_contiguous_complete_layout() -> None:
    output = "\n".join(
        (
            "Flash Region 0 (Flash Descriptor): 00000000 - 00000fff",
            "Flash Region 1 (GbE): 00001000 - 00002fff",
            "Flash Region 2 (Intel ME): 00003000 - 004fffff",
            "Flash Region 3 (BIOS): 00500000 - 007fffff",
        )
    )
    regions = parse_ifdtool_regions(output, 8 * 1024 * 1024)
    assert [(r.name, r.start_byte, r.end_byte) for r in regions] == [
        ("ifd", 0, 0x1000),
        ("gbe", 0x1000, 0x3000),
        ("me", 0x3000, 0x500000),
        ("bios", 0x500000, 0x800000),
    ]
    compare_region_maps(regions, regions)
    with pytest.raises(ValueError):
        compare_region_maps(regions[:-1], regions)
    with pytest.raises(ValueError, match="gap"):
        parse_ifdtool_regions(output.replace("00001000", "00002000"), 8 * 1024 * 1024)
    with pytest.raises(ValueError, match="3 MiB"):
        parse_ifdtool_regions(
            output.replace("004fffff", "005fffff").replace("00500000", "00600000"),
            8 * 1024 * 1024,
        )


def test_x220_full_chip_preservation_requires_exact_original_non_bios_regions() -> None:
    size = 8 * 1024 * 1024
    regions = (
        RegionEvidence("ifd", 0, 0x1000),
        RegionEvidence("gbe", 0x1000, 0x3000),
        RegionEvidence("me", 0x3000, 0x500000),
        RegionEvidence("bios", 0x500000, size),
    )
    original = bytes((index % 251 for index in range(size)))
    generated = bytearray(original)
    generated[0x500000:] = b"\xA5" * (size - 0x500000)
    assert validate_x220_region_preservation(original, bytes(generated), regions) == ("gbe", "ifd", "me")

    generated[0x3000] ^= 0x01
    with pytest.raises(ValueError, match="me region differs"):
        validate_x220_region_preservation(original, bytes(generated), regions)


def test_windows_build_fails_before_subprocess_or_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import autoloader.rom_builder.artifact_engine as module

    def forbidden(*args, **kwargs):
        raise AssertionError("Windows guard must not spawn a subprocess")

    monkeypatch.setattr(module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(module.subprocess, "run", forbidden)
    output = tmp_path / "artifacts"
    result = ArtifactEngine().build(
        BuildRequest("x200-libreboot-4mb", tmp_path / "not-a-checkout", output)
    )
    assert result.status is BuildStatus.BLOCKED
    assert result.code == "LINUX_BUILD_ENV_REQUIRED"
    assert "No command was run" in result.message
    assert result.artifacts == ()
    assert not output.exists()
    assert result.manifest is None


def test_source_inspection_requires_exact_commit_tree_cleanliness_and_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autoloader.rom_builder.artifact_engine as module

    source = tmp_path / "source"
    source.mkdir()
    file_data = {"target.cfg": b"payload_seabios=\"y\"\n", "config": b"pinned-mode-config\n"}
    pinned_files = tuple(
        PinnedFile(name, hashlib.sha256(data).hexdigest())
        for name, data in file_data.items()
    )
    for name, data in file_data.items():
        (source / name).write_bytes(data)
    pin = SourcePin(
        name="test-pin",
        repository_url="https://example.invalid/lbmk.git",
        revision="a" * 40,
        tree_id="b" * 40,
        files=pinned_files,
    )
    recipe = replace(X200_4MB, source=pin)
    monkeypatch.setattr(module, "ARTIFACT_RECIPES", {recipe.recipe_id: recipe})
    git_values = {
        ("remote", "get-url", "origin"): pin.repository_url,
        ("rev-parse", "HEAD"): pin.revision,
        ("rev-parse", "HEAD^{tree}"): pin.tree_id,
        ("status", "--porcelain=v1", "--untracked-files=all"): "",
    }
    engine = ArtifactEngine()
    monkeypatch.setattr(engine, "_git_value", lambda _source, *args: git_values[args])

    audit = engine.inspect_source(recipe.recipe_id, source)
    assert audit.clean is True
    assert audit.revision == pin.revision
    assert audit.tree_id == pin.tree_id
    assert audit.pinned_file_hashes == tuple((item.path, item.sha256) for item in pinned_files)

    git_values[("remote", "get-url", "origin")] = "https://example.invalid/attacker.git"
    with pytest.raises(ValueError, match="origin"):
        engine.inspect_source(recipe.recipe_id, source)
    git_values[("remote", "get-url", "origin")] = pin.repository_url
    git_values[("rev-parse", "HEAD")] = "c" * 40
    with pytest.raises(ValueError, match="revision/tree"):
        engine.inspect_source(recipe.recipe_id, source)
    git_values[("rev-parse", "HEAD")] = pin.revision
    git_values[("status", "--porcelain=v1", "--untracked-files=all")] = "?? local-change"
    with pytest.raises(ValueError, match="dirty"):
        engine.inspect_source(recipe.recipe_id, source)
    git_values[("status", "--porcelain=v1", "--untracked-files=all")] = ""
    (source / "config").write_bytes(b"changed config\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        engine.inspect_source(recipe.recipe_id, source)


def _populate_lbmk_output_fixture(source: Path, recipe) -> tuple[Path, ...]:
    target_config = source / "config" / "coreboot" / recipe.target
    config_dir = target_config / "config"
    config_dir.mkdir(parents=True)
    for stem in ("libgfxinit_corebootfb", "libgfxinit_txtmode"):
        (config_dir / stem).write_text("CONFIG_PAYLOAD_NONE=y\n", encoding="utf-8")
    keymap_dir = source / "config" / "data" / "grub" / "keymap"
    keymap_dir.mkdir(parents=True)
    from autoloader.rom_builder.artifact_engine import _PINNED_LBMK_KEYMAPS

    for name in _PINNED_LBMK_KEYMAPS:
        (keymap_dir / f"{name}.gkb").write_bytes(b"fixture keymap")
    target_dir = source / "bin" / recipe.target
    target_dir.mkdir(parents=True)
    outputs = lbmk_expected_output_paths(source, recipe)
    for relative in outputs:
        (source / relative).write_bytes(b"rom")
    return tuple(source / relative for relative in outputs)


def test_lbmk_validates_normal_multi_output_and_selects_only_exact_seabios(tmp_path: Path) -> None:
    recipe = replace(X200_4MB, expected_size_bytes=3)
    source = tmp_path / "lbmk"
    source.mkdir()
    engine = ArtifactEngine()
    engine._assert_no_stale_outputs(recipe, source)
    (source / "bin").mkdir()
    with pytest.raises(ValueError, match="Stale"):
        engine._assert_no_stale_outputs(recipe, source)
    (source / "bin").rmdir()

    outputs = _populate_lbmk_output_fixture(source, recipe)
    assert len(outputs) == 31
    assert len({path.name for path in outputs}) == 31
    assert select_exact_lbmk_artifact(source, recipe) == source / recipe.output_relative_path

    (source / "bin" / recipe.target / "unexpected.rom").write_bytes(b"rom")
    with pytest.raises(ValueError, match="inventory mismatch"):
        select_exact_lbmk_artifact(source, recipe)
    (source / "bin" / recipe.target / "unexpected.rom").unlink()

    selected = source / recipe.output_relative_path
    selected.unlink()
    with pytest.raises(ValueError, match="inventory mismatch"):
        select_exact_lbmk_artifact(source, recipe)
    selected.write_bytes(b"rom-too-large")
    with pytest.raises(ValueError, match="chip capacity"):
        select_exact_lbmk_artifact(source, recipe)


def test_lbmk_rejects_casefolded_duplicate_output_candidates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recipe = replace(X200_4MB, expected_size_bytes=3)
    source = tmp_path / "lbmk"
    source.mkdir()
    outputs = _populate_lbmk_output_fixture(source, recipe)
    target_dir = source / "bin" / recipe.target
    selected = source / recipe.output_relative_path
    original_iterdir = Path.iterdir

    def iter_with_collision(path: Path):
        entries = list(original_iterdir(path))
        if path == target_dir:
            entries.append(selected.with_name(selected.name.upper()))
        return iter(entries)

    monkeypatch.setattr(Path, "iterdir", iter_with_collision)
    with pytest.raises(ValueError, match="colliding ROM filenames"):
        select_exact_lbmk_artifact(source, recipe)
    assert len(outputs) == 31


def test_lbmk_rejects_symlinked_expected_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recipe = replace(X200_4MB, expected_size_bytes=3)
    source = tmp_path / "lbmk"
    source.mkdir()
    _populate_lbmk_output_fixture(source, recipe)
    selected = source / recipe.output_relative_path
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: path == selected or original_is_symlink(path),
    )
    with pytest.raises(ValueError, match="regular non-symlink"):
        select_exact_lbmk_artifact(source, recipe)


def test_source_and_output_roots_must_not_overlap(tmp_path: Path) -> None:
    from autoloader.rom_builder.artifact_engine import _require_disjoint_paths

    source = tmp_path / "checkout"
    nested_output = source / "artifacts"
    source.mkdir()
    with pytest.raises(ValueError, match="separate"):
        _require_disjoint_paths(source, nested_output)
    with pytest.raises(ValueError, match="separate"):
        _require_disjoint_paths(source / "nested", source)
    _require_disjoint_paths(source, tmp_path / "external-output")


def test_t430_build_request_is_official_handoff_only(tmp_path: Path) -> None:
    result = ArtifactEngine().build(
        BuildRequest("t430-1vyrain-upstream-handoff", tmp_path / "none", tmp_path / "output")
    )
    assert result.status is BuildStatus.HANDOFF_REQUIRED
    assert result.artifacts == ()
    assert result.manifest is None
    assert "official" in result.message.lower()
    assert not (tmp_path / "output").exists()


def test_unknown_recipe_never_selects_a_firmware_family(tmp_path: Path) -> None:
    result = ArtifactEngine().build(BuildRequest("coreboot-master-x230", tmp_path, tmp_path / "out"))
    assert result.status is BuildStatus.BLOCKED
    assert result.code == "UNKNOWN_ARTIFACT_RECIPE"
    assert not (tmp_path / "out").exists()


def test_manifest_is_immutable_and_hashes_canonical_content() -> None:
    manifest = ArtifactManifest(
        schema_version=3,
        operation_id="operation-1",
        created_at_utc="2026-10-10T00:00:00+00:00",
        recipe_id="x220-coreboot-8mb-seabios",
        workflow_id="x220-coreboot",
        status="failed",
        source_repository_url=COREBOOT_2609.repository_url,
        source_revision=COREBOOT_2609.revision,
        source_tree_id=COREBOOT_2609.tree_id,
        source_files=(),
        commands=(CommandRecord(("make", "olddefconfig"), 1, "logs/01.log", "a" * 64),),
        host_platform="test host",
        toolchain=(("make", "GNU Make"),),
        build_environment=(("LC_ALL", "C"),),
        container_image_digest=None,
        configuration_sha256="b" * 64,
        resolved_configuration_sha256=None,
        observed_chip_capacity_bytes=8 * 1024 * 1024,
        expected_regions=(),
        blob_sha256=(),
        artifacts=(),
        selected_artifact_relative_path=None,
        selected_artifact_sha256=None,
        failure_code="TEST_FAILURE",
        failure_detail="Fake command failure; no firmware build performed.",
        reproducibility=ReproducibilityRecord("not_attempted", None, None, "No compile run."),
    )
    assert len(manifest.sha256) == 64
    assert manifest.sha256 == hashlib.sha256(manifest.canonical_json().encode()).hexdigest()
    with pytest.raises(FrozenInstanceError):
        manifest.status = "complete"  # type: ignore[misc]
    with pytest.raises(ValueError, match="selected artifact"):
        replace(manifest, status="complete", failure_code=None, artifacts=())


def test_fake_command_failure_is_logged_and_never_returns_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import autoloader.rom_builder.artifact_engine as module

    run_dir = tmp_path / "run"
    logs = run_dir / "logs"
    logs.mkdir(parents=True)
    _ensure_operation_git_config(run_dir)
    observed: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        observed.update(kwargs)
        return SimpleNamespace(returncode=2, stdout="", stderr="fake failure")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    commands: list[CommandRecord] = []
    with pytest.raises(RuntimeError, match="exit code 2"):
        ArtifactEngine()._run_logged(("fake-tool", "build"), tmp_path, logs, commands)
    assert len(commands) == 1
    assert commands[0].exit_code == 2
    assert Path(logs.parent / commands[0].log_relative_path).is_file()
    env = observed["env"]
    assert isinstance(env, dict)
    assert "MAKEFLAGS" not in env
    assert env["HOME"] == str(run_dir)
    assert env["GIT_CONFIG_GLOBAL"] == str(run_dir / "gitconfig")
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert (run_dir / "gitconfig").read_bytes() == _BUILD_GIT_CONFIG_BYTES
    assert hashlib.sha256((run_dir / "gitconfig").read_bytes()).hexdigest() == _BUILD_GIT_CONFIG_SHA256


def test_operation_git_config_isolated_from_operator_global_config(tmp_path: Path) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("UNVALIDATED: Git executable is unavailable for the read-only config check")
    run_dir = tmp_path / "operation"
    run_dir.mkdir()
    config_path, config_digest = _ensure_operation_git_config(run_dir)
    manifest_environment = dict(_build_environment_record(config_digest))
    assert manifest_environment["GIT_CONFIG_GLOBAL_SHA256"] == config_digest
    assert manifest_environment["GIT_CONFIG_GLOBAL"] == "<operation-output-directory>/gitconfig"
    operator_config = tmp_path / ".gitconfig"
    operator_config.write_text("[user]\n\tname = Operator Global Identity\n", encoding="utf-8")
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
        "GIT_CONFIG_GLOBAL": str(config_path),
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    result = subprocess.run(
        [git, "config", "--global", "user.name"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=10,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "Libre Core AutoLoader Build"
    assert config_digest == _BUILD_GIT_CONFIG_SHA256
    assert operator_config.read_text(encoding="utf-8") == "[user]\n\tname = Operator Global Identity\n"


@pytest.mark.parametrize("child_action", ["modify", "delete"])
def test_operation_git_config_mutation_during_child_blocks_command_success(
    child_action: str,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autoloader.rom_builder.artifact_engine as module

    logs = tmp_path / "operation" / "logs"
    logs.mkdir(parents=True)
    _ensure_operation_git_config(logs.parent)

    def fake_run(argv, **kwargs):
        config_path = Path(kwargs["env"]["GIT_CONFIG_GLOBAL"])
        if child_action == "delete":
            config_path.unlink()
        else:
            config_path.write_text("[user]\n\tname = changed by child\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    commands: list[CommandRecord] = []
    with pytest.raises(RuntimeError, match="Git config changed"):
        ArtifactEngine()._run_logged(("fake-tool", "build"), tmp_path, logs, commands)
    assert len(commands) == 1
    assert commands[0].exit_code == 0
    assert (logs.parent / "gitconfig").exists() is (child_action != "delete")


@pytest.mark.parametrize("postprocess_action", ["modify", "delete"])
def test_operation_git_config_mutation_during_artifact_postprocessing_never_publishes_complete(
    postprocess_action: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The final manifest boundary revalidates config after output inspection."""
    import autoloader.rom_builder.artifact_engine as module
    from autoloader.workflow.x200_libreboot import create_x200_build_intent

    recipe = X200_4MB
    source = tmp_path / "pinned-source"
    source.mkdir()
    output = tmp_path / "artifacts"
    intent = create_x200_build_intent(
        device=DeviceEvidence(
            raw_dmi_product_name="ThinkPad X200",
            raw_dmi_product_version="7458CTO",
            raw_dmi_system_family="ThinkPad X200",
            raw_mtm="7458CTO",
        ),
        topology=ChipTopology(
            observed=True,
            chips=(ChipEvidence("flash0", recipe.expected_size_bytes, True, physical_label="main", voltage_mv=3300),),
        ),
        programmer=ProgrammerEvidence(
            kind=ProgrammerKind.EXTERNAL_SPI,
            observed=True,
            interface_verified=True,
            wiring_verified=True,
            voltage_mv=3300,
        ),
    )
    request = BuildRequest(
        recipe_id=recipe.recipe_id,
        source_dir=source,
        output_root=output,
        observed_chip_capacity_bytes=recipe.expected_size_bytes,
        build_intent=intent,
    )
    engine = ArtifactEngine()
    monkeypatch.setattr(
        engine,
        "inspect_source",
        lambda _recipe_id, _source: module.SourceAudit(
            recipe.source.repository_url,
            recipe.source.revision,
            recipe.source.tree_id,
            True,
            tuple((item.path, item.sha256) for item in recipe.source.files),
        ),
    )
    monkeypatch.setattr(engine, "_assert_no_stale_outputs", lambda _recipe, _source: None)
    monkeypatch.setattr(engine, "_validate_lbmk_target", lambda _recipe, _source: None)
    monkeypatch.setattr(engine, "_toolchain_versions", lambda: (("git.version", "fixture"),))
    monkeypatch.setattr(module, "validate_recipe_config", lambda _recipe, _config: "a" * 64)

    objcopy = tmp_path / "fixture-objcopy"
    objcopy.write_bytes(b"Synthetic GNU objcopy identity; not executed.\n")
    commits = tuple(f"{index:x}" * 40 for index in range(1, 9))
    dependency = module.X200UBootDependencyEvidence(
        source_url=module._UBOOT_X86_64_SOURCE_URL,
        backup_url=module._UBOOT_X86_64_BACKUP_URL,
        package_config_sha256=module._UBOOT_PACKAGE_CFG_SHA256,
        target_config_sha256=module._UBOOT_X86_64_TARGET_CFG_SHA256,
        amd64_target_config_sha256=module._UBOOT_AMD64_TARGET_CFG_SHA256,
        amd64_default_config_sha256=module._UBOOT_AMD64_DEFAULT_SHA256,
        target_tree="x86_64",
        base_revision=module._UBOOT_X86_64_BASE_REVISION,
        base_tree_id=module._UBOOT_X86_64_BASE_TREE,
        base_config_sha256=module._UBOOT_X86_64_BASE_CONFIG_SHA256,
        overlay_path=module._X200_UBOOT_OVERLAY_PATH,
        overlay_sha256=module._X200_UBOOT_OVERLAY_SHA256,
        patched_config_sha256=module._UBOOT_X86_64_PATCHED_CONFIG_SHA256,
        patch_inputs=module._X200_UBOOT_PATCH_STACK,
        patch_commits=commits,
        resulting_revision=commits[-1],
        resulting_tree_id=module._UBOOT_X86_64_PATCHED_TREE,
        cache_origin_relative_path=module._uboot_cache_relative_path(module._UBOOT_X86_64_SOURCE_URL),
        resulting_origin_url=module._UBOOT_X86_64_SOURCE_URL,
        base_ancestor_verified=True,
        tracked_worktree_clean=True,
        objcopy_path=str(objcopy.resolve()),
        objcopy_sha256=hashlib.sha256(objcopy.read_bytes()).hexdigest(),
        objcopy_version="GNU objcopy fixture; no tool run",
    )
    monkeypatch.setattr(engine, "_prepare_x200_lbmk_build_source", lambda _recipe, _source, _run, _logs, _commands: (source, dependency))
    synthetic_nested = _synthetic_x200_nested_repository_evidence()
    monkeypatch.setattr(engine, "_stage_x200_vboot_compat_overlay", lambda _source: None)
    monkeypatch.setattr(
        engine,
        "_validate_lbmk_nested_coreboot",
        lambda _source, _logs, _commands: synthetic_nested,
    )
    monkeypatch.setattr(
        engine,
        "_validate_x200_uboot_dependency_after_build",
        lambda _recipe, _source, _run, _logs, _commands, previous: previous,
    )

    def fake_prepare_grub_font(_source, run_dir, _logs, _commands):
        staged_font = run_dir / "inputs" / "unifont.otf"
        staged_font.parent.mkdir()
        staged_font.write_bytes(b"Synthetic pinned-font fixture; no build performed.\n")
        return staged_font

    def fake_collect_grub_font(_source, _run, _font, _logs, _commands):
        return module.X200GrubFontEvidence(
            source_font_path=module._X200_GRUB_FONT_SOURCE_PATH,
            source_font_sha256=module._X200_GRUB_FONT_SOURCE_SHA256,
            source_package=module._X200_GRUB_FONT_PACKAGE,
            source_package_version=module._X200_GRUB_FONT_PACKAGE_VERSION,
            staged_font_relative_path=module._X200_GRUB_FONT_STAGED_RELATIVE_PATH,
            staged_font_sha256=module._X200_GRUB_FONT_SOURCE_SHA256,
            overlay_resource_path=module._X200_GRUB_FONT_OVERLAY_PATH,
            overlay_resource_sha256=module._X200_GRUB_FONT_OVERLAY_SHA256,
            lbmk_tree_sh_sha256=module._X200_LBMK_TREE_SH_SHA256,
            lbmk_tree_sh_patched_sha256=module._X200_LBMK_TREE_SH_PATCHED_SHA256,
            grub_source_relative_path="work/lbmk/src/grub/default",
            grub_repository_url=module._X200_GRUB_SOURCE_URL,
            grub_cache_relative_path=module._X200_GRUB_CACHE_RELATIVE_PATH,
            grub_base_revision=module._X200_GRUB_BASE_REVISION,
            grub_base_tree_id=module._X200_GRUB_BASE_TREE,
            grub_patch_inputs=module._X200_GRUB_PATCH_INPUTS,
            grub_patch_commits=tuple(f"{index:040x}" for index in range(1, 11)),
            grub_patch_tree_ids=module._X200_GRUB_PATCH_TREE_IDS,
            grub_result_revision=f"{10:040x}",
            grub_result_tree_id=module._X200_GRUB_PATCHED_TREE,
            configure_log_relative_path=module._X200_GRUB_CONFIG_LOG_RELATIVE_PATH,
            configure_log_sha256="c" * 64,
            configure_argv=(
                "./configure",
                "--with-unifont=<operation-output-directory>/inputs/unifont.otf",
                *module._X200_GRUB_CONFIGURE_FIXED_ARGS,
            ),
            generated_payload_relative_path=module._X200_GRUB_PAYLOAD_RELATIVE_PATH,
            generated_payload_sha256="d" * 64,
        )

    monkeypatch.setattr(engine, "_prepare_x200_grub_font", fake_prepare_grub_font)
    monkeypatch.setattr(engine, "_collect_x200_grub_font_evidence", fake_collect_grub_font)
    monkeypatch.setattr(engine, "_validate_x200_grub_font_evidence", lambda _operation, _evidence: None)

    selected_raw = source / module.PurePosixPath(recipe.output_relative_path)

    def fake_build_command(argv, _cwd, logs_dir, commands, **_kwargs):
        build_log = logs_dir / "01-build.log"
        build_log.write_text("Synthetic build command; no compilation performed.\n", encoding="utf-8")
        commands.append(CommandRecord(
            tuple(argv),
            0,
            "logs/01-build.log",
            hashlib.sha256(build_log.read_bytes()).hexdigest(),
        ))
        resolved_config = source / "src" / "coreboot" / "default" / ".config"
        resolved_config.parent.mkdir(parents=True)
        resolved_config.write_text("synthetic resolved config\n", encoding="utf-8")
        selected_raw.parent.mkdir(parents=True)
        selected_raw.write_bytes(b"R" * recipe.expected_size_bytes)

    monkeypatch.setattr(engine, "_run_logged", fake_build_command)
    monkeypatch.setattr(module, "validated_lbmk_artifacts", lambda _source, _recipe: (selected_raw,))

    def mutate_during_cbfs_inspection(_recipe, _source, _artifact, logs_dir, _commands):
        git_config = logs_dir.parent / "gitconfig"
        if postprocess_action == "delete":
            git_config.unlink()
        else:
            git_config.write_text("[user]\n\tname = altered after build\n", encoding="utf-8")
        coreboot_evidence = next(item for item in synthetic_nested if item.role == "coreboot")
        return module.CBFSInspectionRecord(
            tool_relative_path="fixture/cbfstool",
            tool_sha256="b" * 64,
            source_repository_url=coreboot_evidence.repository_url,
            source_revision=coreboot_evidence.resulting_revision,
            source_tree_id=coreboot_evidence.resulting_tree_id,
            region="whole_flash",
            required_payload="fallback/payload",
            inventory=(module.CBFSEntry("fallback/payload", 0, "payload", 0, 1, 1),),
            source_file_hashes=tuple((item.path, item.sha256) for item in module._LBMK_COREBOOT_CBFSTOOL_SOURCES),
            nested_repositories=synthetic_nested,
        )

    monkeypatch.setattr(engine, "_inspect_cbfs", mutate_during_cbfs_inspection)

    result = engine._build_linux(recipe, request)

    assert result.status is BuildStatus.FAILED
    assert result.code == "ARTIFACT_VALIDATION_FAILED"
    assert result.manifest is not None
    assert result.manifest.status == "failed"
    assert result.manifest.failure_code == "ARTIFACT_VALIDATION_FAILED"
    assert "final artifact evidence validation" in (result.manifest.failure_detail or "").lower()
    assert result.artifacts == ()


def test_operation_git_config_refuses_modified_existing_file(tmp_path: Path) -> None:
    from autoloader.rom_builder.artifact_engine import _ensure_operation_git_config

    operation = tmp_path / "operation"
    operation.mkdir()
    gitconfig = operation / "gitconfig"
    gitconfig.write_text("[user]\n\tname = unexpected\n", encoding="utf-8")
    if os.name != "nt":
        gitconfig.chmod(0o600)
    with pytest.raises(ValueError, match="differs from the pinned deterministic identity"):
        _ensure_operation_git_config(operation)


@pytest.mark.skipif(os.name == "nt", reason="UNVALIDATED: POSIX permission bits are not meaningful on Windows")
def test_operation_git_config_rejects_group_or_world_access(tmp_path: Path) -> None:
    from autoloader.rom_builder.artifact_engine import _ensure_operation_git_config

    operation = tmp_path / "operation"
    operation.mkdir()
    config_path, _ = _ensure_operation_git_config(operation)
    config_path.chmod(0o644)
    with pytest.raises(ValueError, match="permissions must be owner-only"):
        _ensure_operation_git_config(operation)


def test_fake_command_timeout_is_recorded_as_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import autoloader.rom_builder.artifact_engine as module

    logs = tmp_path / "run" / "logs"
    logs.mkdir(parents=True)
    _ensure_operation_git_config(logs.parent)

    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1, output="timeout")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    commands: list[CommandRecord] = []
    with pytest.raises(RuntimeError, match="timed out"):
        ArtifactEngine()._run_logged(("fake-tool", "build"), tmp_path, logs, commands)
    assert len(commands) == 1
    assert commands[0].exit_code == -1
