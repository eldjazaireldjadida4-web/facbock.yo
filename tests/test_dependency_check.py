from dependency_check import RUNTIME_IMPORTS, missing_runtime_dependencies


def test_missing_dependencies_are_reported_by_install_package_name():
    def importer(module_name):
        if module_name == "portalocker":
            raise ModuleNotFoundError(module_name)
        return object()

    assert missing_runtime_dependencies(importer) == ["portalocker"]


def test_runtime_check_covers_control_plane_lock_dependency():
    assert ("portalocker", "portalocker") in RUNTIME_IMPORTS


def test_runtime_check_includes_gui_tkinter_system_dependency():
    assert any(module == "tkinter" and "python3-tk" in package for module, package in RUNTIME_IMPORTS)
