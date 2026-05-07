# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec para Pocket Option Bot

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('src', 'src'),
        ('.env.example', '.'),
        ('ejemplo.md', '.'),
    ],
    hiddenimports=[
        # dotenv
        'dotenv',
        'python_dotenv',
        # telethon
        'telethon',
        'telethon.network',
        'telethon.network.mtproto',
        'telethon.network.connection',
        'telethon.network.connection.tcpfull',
        'telethon.crypto',
        'telethon.crypto.rsa',
        'telethon.tl',
        'telethon.tl.types',
        'telethon.tl.functions',
        'telethon.extensions',
        'telethon.sessions',
        # playwright
        'playwright',
        'playwright.async_api',
        'playwright.sync_api',
        'playwright._impl',
        'playwright._impl._driver',
        'playwright._impl._browser',
        'playwright._impl._browser_context',
        'playwright._impl._page',
        # zoneinfo
        'zoneinfo',
        'zoneinfo._common',
        # tkinter (wizard de configuracion)
        'tkinter',
        'tkinter.messagebox',
        'tkinter.ttk',
        # bot modules
        'src',
        'src.config',
        'src.config.settings',
        'src.core',
        'src.core.engine',
        'src.core.models',
        'src.core.pipeline',
        'src.core.console_hub',
        'src.pocket_option',
        'src.pocket_option.client',
        'src.pocket_option.assets',
        'src.pocket_option.candle_feed',
        'src.pocket_option.trade_panel_feed',
        'src.signals',
        'src.signals.parser',
        'src.telegram',
        'src.telegram.reader',
        'src.telegram.message_types',
        'src.utils',
        'src.utils.blackbox',
        'src.utils.logger',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy', 'pandas', 'PIL'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='PocketOptionBot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    icon=None,
)
