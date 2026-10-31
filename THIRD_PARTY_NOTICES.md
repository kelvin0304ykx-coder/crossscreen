# Third-party components

CrossScreen provides its own interface, pairing configuration and process supervision. The bundled input engine is an unmodified upstream Deskflow binary.

| Component | Version | License / corresponding source |
| --- | --- | --- |
| Deskflow | 1.27.0 | GPL-2.0-only WITH LicenseRef-OpenSSL-Exception. Original license and exception are in `licenses/`. The matching complete source archive is a release asset. [Tagged source](https://github.com/deskflow/deskflow/tree/v1.27.0). |
| Qt / PySide6 / Shiboken | 6.12.0 | The included Qt Core, Gui, Widgets, Network and Svg components retain their upstream license choices. LGPL-3.0 and GPL texts are in `licenses/`. [PySide source](https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v6.12.0), [Qt source](https://code.qt.io/cgit/qt/qtbase.git/tag/?h=v6.12.0), [Qt licensing documentation](https://doc.qt.io/qtforpython-6/licenses.html). |
| Python | 3.12.10 Windows / 3.11.15 Mac | Python Software Foundation license and notices are included. [Python source](https://www.python.org/downloads/source/). |
| cryptography | 46.0.7 Windows / 50.0.2 Mac | Apache-2.0 OR BSD-3-Clause. License texts are included. [Corresponding source](https://github.com/pyca/cryptography). |
| cffi / pycparser | bundled runtime | Original MIT / BSD notices are included. [cffi](https://github.com/python-cffi/cffi), [pycparser](https://github.com/eliben/pycparser). |
| PyInstaller bootloader | 6.22.3 | Original license with the bootloader distribution exception is included. [Tagged source](https://github.com/pyinstaller/pyinstaller/tree/v6.22.3). |

Libraries are used without source modifications and shipped as separate replaceable files. The public repository provides the interface source and build instructions. To rebuild with compatible library versions, install the requirements in a virtual environment and run `python tools/build.py` on the target platform. Application configuration and device identity are stored outside the program directory.

Additional notices that come with the upstream engine remain in its original distribution. Trademarks belong to their respective owners.
