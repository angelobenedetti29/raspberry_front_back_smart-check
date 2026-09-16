"""Sistema de diseño de la aplicación de escritorio Factory Control.

El módulo define los design tokens (colores, espaciados, radios y tipografía) y
construye a partir de ellos la hoja de estilos de la aplicación. Todos los
widgets de ``frontend.ui`` se estilan mediante ``objectName`` y propiedades
dinámicas definidas aquí, de modo que ningún panel escribe un color crudo.

La dirección visual es un *industrial dark* sobrio: base carbón/acero, un único
acento azul acero contenido y una paleta semántica pequeña.
"""

from string import Template
import os

# ---------------------------------------------------------------------------
# Tokens de color
# ---------------------------------------------------------------------------
# Superficies base: carbón y acero.
BG = "#0E1317"
SURFACE = "#151B21"
SURFACE_2 = "#1B242B"
SURFACE_3 = "#222D35"
SURFACE_SUNKEN = "#0B0F13"

BORDER = "#2A363F"
BORDER_STRONG = "#3B4B57"

# Colores de tipografía.
TEXT = "#DCE5EC"
TEXT_MUTED = "#8A99A6"
# Subido desde #5F6E79 para que el texto secundario de 11-12px alcance WCAG AA
# (>= 4.5:1) sobre las tarjetas sin dejar de verse apagado.
TEXT_DIM = "#7C8B98"
TEXT_INVERT = "#0B1014"
# Texto sobre superficies rojas sólidas (claro, por contraste).
TEXT_ON_DANGER = "#FFFFFF"

# Único acento contenido: azul acero.
ACCENT = "#4C9BE8"
ACCENT_HOVER = "#6BB0F0"
ACCENT_PRESS = "#3A7FC4"
ACCENT_SOFT = "rgba(76, 155, 232, 0.14)"

# Colores semánticos.
SUCCESS = "#43B76A"
SUCCESS_SOFT = "rgba(67, 183, 106, 0.14)"
SUCCESS_SOFT_HOVER = "rgba(67, 183, 106, 0.22)"

WARNING = "#D9A441"
WARNING_SOFT = "rgba(217, 164, 65, 0.14)"
WARNING_BORDER = "rgba(217, 164, 65, 0.30)"
WARNING_TEXT = "#E6C583"

DANGER = "#E0574E"
DANGER_PRESS = "#C4453D"
DANGER_SOFT = "rgba(224, 87, 78, 0.13)"
DANGER_BORDER = "rgba(224, 87, 78, 0.28)"
# Tono de peligro más claro para texto sobre la superficie suave (>= 4.5:1).
DANGER_TEXT = "#F08F88"
DANGER_TEXT_STRONG = "#F0ACA6"

INFO = "#4C9BE8"
INFO_SOFT = "rgba(76, 155, 232, 0.14)"
INFO_BORDER = "rgba(76, 155, 232, 0.30)"
INFO_TEXT = "#A9D0F5"

NEUTRAL_SOFT = "rgba(255, 255, 255, 0.05)"

# ---------------------------------------------------------------------------
# Escala de espaciado (px)
# ---------------------------------------------------------------------------
SP_XS = 4
SP_SM = 8
SP_MD = 12

# ---------------------------------------------------------------------------
# Radios de borde (px)
# ---------------------------------------------------------------------------
RADIUS_SM = 4
RADIUS_MD = 8
RADIUS_LG = 12
RADIUS_PILL = 10

# ---------------------------------------------------------------------------
# Tipografía
# ---------------------------------------------------------------------------
FONT_FAMILY = "'IBM Plex Sans', 'Barlow', 'Segoe UI', 'Roboto', sans-serif"

FS_XS = 11
FS_SM = 12
FS_BASE = 13
FS_MD = 14
FS_XL = 20

FW_MEDIUM = 500
FW_SEMIBOLD = 600
FW_BOLD = 700

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
SIDEBAR_MIN_WIDTH = 210
SIDEBAR_MAX_WIDTH = 260
RIGHT_COLUMN_MIN_WIDTH = 300


def _collect_tokens() -> dict:
    """Recoge como tokens todas las constantes en MAYÚSCULAS de este módulo.

    Se evita así mantener a mano un diccionario espejo: al declarar un token
    nuevo (color, espaciado, radio, tipografía...) queda disponible para la QSS
    sin ningún paso extra.

    Se excluyen los nombres que empiezan por ``_`` (privados como
    ``_ASSETS_DIR``) y al propio ``TOKENS``.
    """
    return {
        name: value
        for name, value in globals().items()
        if name.isupper() and not name.startswith("_") and name != "TOKENS"
    }


# Mapa público de tokens usado para expandir la plantilla del stylesheet.
TOKENS = _collect_tokens()

_STYLESHEET = Template(
    """
/* ------------------------------------------------------------------ base */
QMainWindow, QWidget#Root {
    background-color: $BG;
}
QWidget {
    font-family: $FONT_FAMILY;
    font-size: ${FS_BASE}px;
    color: $TEXT;
}
QLabel {
    background: transparent;
    color: $TEXT;
    font-family: $FONT_FAMILY;
    font-size: ${FS_BASE}px;
}
QToolTip {
    background-color: $SURFACE_3;
    color: $TEXT;
    border: 1px solid $BORDER_STRONG;
    padding: 4px 6px;
}

/* -------------------------------------------------------- barra lateral */
#Sidebar {
    background-color: $SURFACE;
    border: 1px solid $BORDER;
    border-radius: $RADIUS_LG;
}
#Brand {
    color: $TEXT;
    font-size: ${FS_XL}px;
    font-weight: $FW_BOLD;
}
#BrandSub {
    color: $TEXT_DIM;
    font-size: ${FS_XS}px;
    font-weight: $FW_SEMIBOLD;
}
#SidebarGroup {
    color: $TEXT_DIM;
    font-size: ${FS_XS}px;
    font-weight: $FW_BOLD;
    padding-left: 2px;
}

/* -------------------------------------------------- botón de navegación */
QPushButton[variant="nav"] {
    background-color: transparent;
    color: $TEXT_MUTED;
    text-align: left;
    padding: 10px 14px;
    border: 1px solid transparent;
    border-radius: $RADIUS_MD;
    font-weight: $FW_SEMIBOLD;
    font-size: ${FS_BASE}px;
}
QPushButton[variant="nav"]:hover {
    background-color: $SURFACE_2;
    color: $TEXT;
}
QPushButton[variant="nav"]:pressed {
    background-color: $SURFACE_3;
}
QPushButton[variant="nav"]:focus {
    border: 1px solid $BORDER_STRONG;
}
QPushButton[variant="nav"]:checked {
    background-color: $ACCENT_SOFT;
    color: $ACCENT;
    border: 1px solid $ACCENT;
}
/* Conserva el borde de acento cuando un item de navegación marcado además
   tiene el foco de teclado. */
QPushButton[variant="nav"]:checked:focus {
    background-color: $ACCENT_SOFT;
    color: $ACCENT;
    border: 1px solid $ACCENT;
}

/* --------------------------------------------------------------- combo */
QComboBox {
    background-color: $SURFACE_2;
    border: 1px solid $BORDER;
    border-radius: $RADIUS_MD;
    padding: 8px 12px;
    color: $TEXT;
    font-size: ${FS_BASE}px;
    font-weight: $FW_MEDIUM;
}
QComboBox:hover {
    border: 1px solid $BORDER_STRONG;
}
QComboBox:focus {
    border: 1px solid $ACCENT;
}
QComboBox:disabled {
    color: $TEXT_DIM;
    background-color: $SURFACE;
}
QComboBox::drop-down {
    border: none;
    width: 26px;
}
QComboBox::down-arrow {
    image: url($DOWN_ARROW);
    width: 12px;
    height: 12px;
}
QComboBox QAbstractItemView {
    background-color: $SURFACE_2;
    color: $TEXT;
    border: 1px solid $BORDER_STRONG;
    border-radius: $RADIUS_SM;
    padding: 4px;
    selection-background-color: $ACCENT;
    selection-color: $TEXT_INVERT;
    outline: none;
}

/* ------------------------------------------------------------- botones */
QPushButton {
    background-color: $SURFACE_2;
    color: $TEXT;
    border: 1px solid $BORDER;
    border-radius: $RADIUS_MD;
    padding: 8px 14px;
    font-weight: $FW_SEMIBOLD;
    font-size: ${FS_BASE}px;
}
QPushButton:hover {
    background-color: $SURFACE_3;
    border: 1px solid $BORDER_STRONG;
}
QPushButton:pressed {
    background-color: $SURFACE;
}
QPushButton:focus {
    border: 2px solid $ACCENT;
}
QPushButton:disabled {
    background-color: $SURFACE;
    color: $TEXT_DIM;
    border: 1px solid $BORDER;
}

QPushButton[variant="primary"] {
    background-color: $ACCENT;
    color: $TEXT_INVERT;
    border: 1px solid $ACCENT;
}
QPushButton[variant="primary"]:hover {
    background-color: $ACCENT_HOVER;
    border: 1px solid $ACCENT_HOVER;
}
QPushButton[variant="primary"]:pressed {
    background-color: $ACCENT_PRESS;
    border: 1px solid $ACCENT_PRESS;
}
QPushButton[variant="primary"]:focus {
    border: 2px solid $TEXT;
}
QPushButton[variant="primary"]:disabled {
    background-color: $SURFACE_3;
    color: $TEXT_DIM;
    border: 1px solid $BORDER;
}

QPushButton[variant="danger"] {
    background-color: $DANGER_SOFT;
    color: $DANGER_TEXT;
    border: 1px solid $DANGER;
}
QPushButton[variant="danger"]:hover {
    background-color: $DANGER;
    color: $TEXT_ON_DANGER;
    border: 1px solid $DANGER;
}
QPushButton[variant="danger"]:pressed {
    background-color: $DANGER_PRESS;
    color: $TEXT_ON_DANGER;
    border: 1px solid $DANGER_PRESS;
}
QPushButton[variant="danger"]:focus {
    border: 2px solid $ACCENT;
}
QPushButton[variant="danger"]:disabled {
    background-color: $SURFACE;
    color: $TEXT_DIM;
    border: 1px solid $BORDER;
}

/* ------------------------------------------------------- chip de filtro */
QPushButton#FilterChip {
    text-align: left;
    padding: 9px 12px;
    border-radius: $RADIUS_MD;
    font-size: ${FS_SM}px;
    font-weight: $FW_SEMIBOLD;
}
QPushButton#FilterChip[active="true"] {
    background-color: $SUCCESS_SOFT;
    color: $SUCCESS;
    border: 1px solid $SUCCESS;
}
QPushButton#FilterChip[active="true"]:hover {
    background-color: $SUCCESS_SOFT_HOVER;
}
QPushButton#FilterChip[active="false"] {
    background-color: $SURFACE_2;
    color: $TEXT_DIM;
    border: 1px solid $BORDER;
}
QPushButton#FilterChip[active="false"]:hover {
    background-color: $SURFACE_3;
    color: $TEXT_MUTED;
}
/* El foco de teclado debe verse por encima de los estilos activo/inactivo. */
QPushButton#FilterChip:focus {
    border: 2px solid $ACCENT;
}

/* ------------------------------------------------------------ tarjetas */
QFrame#Card {
    background-color: $SURFACE;
    border: 1px solid $BORDER;
    border-radius: $RADIUS_LG;
}

/* ---------------------------------------------------------- tipografía */
QLabel#SectionTitle {
    color: $TEXT;
    font-size: ${FS_SM}px;
    font-weight: $FW_BOLD;
}
QLabel#SectionMeta {
    color: $TEXT_DIM;
    font-size: ${FS_XS}px;
    font-weight: $FW_SEMIBOLD;
}

/* -------------------------------------------------- píldoras de estado */
QLabel#StatusPill {
    padding: 3px 10px;
    border-radius: $RADIUS_PILL;
    font-size: ${FS_XS}px;
    font-weight: $FW_BOLD;
}
QLabel#StatusPill[tone="on"] {
    background-color: $SUCCESS_SOFT;
    color: $SUCCESS;
}
QLabel#StatusPill[tone="off"] {
    background-color: $NEUTRAL_SOFT;
    color: $TEXT_DIM;
}
QLabel#StatusPill[tone="info"] {
    background-color: $INFO_SOFT;
    color: $INFO;
}
QLabel#StatusPill[tone="warning"] {
    background-color: $WARNING_SOFT;
    color: $WARNING;
}
QLabel#StatusPill[tone="danger"] {
    background-color: $DANGER_SOFT;
    color: $DANGER_TEXT;
}

/* ---------------------------------------------------- superficie vídeo */
QLabel#VideoSurface {
    background-color: $SURFACE_SUNKEN;
    border: 1px solid $BORDER;
    border-radius: $RADIUS_LG;
    color: $TEXT_DIM;
    font-size: ${FS_MD}px;
    font-weight: $FW_MEDIUM;
}
QLabel#VideoSurface[state="off"] {
    color: $TEXT_DIM;
}
QLabel#VideoSurface[state="connecting"] {
    color: $ACCENT;
}
QLabel#VideoSurface[state="live"] {
    color: transparent;
}
QLabel#VideoSurface[state="error"] {
    background-color: $DANGER_SOFT;
    border: 1px solid $DANGER;
    color: $DANGER_TEXT;
    font-weight: $FW_SEMIBOLD;
}
QLabel#VideoSurface[state="recovery"] {
    background-color: $WARNING_SOFT;
    border: 1px solid $WARNING;
    color: $WARNING;
    font-weight: $FW_SEMIBOLD;
}

/* --------------------------------------------------- elementos de lista */
QPushButton#ListItem {
    text-align: left;
    background-color: $SURFACE_2;
    color: $TEXT;
    border: 1px solid transparent;
    border-radius: $RADIUS_MD;
    padding: 9px 12px;
    font-size: ${FS_SM}px;
    font-weight: $FW_MEDIUM;
}
QPushButton#ListItem:hover {
    background-color: $SURFACE_3;
    border: 1px solid $BORDER_STRONG;
}
QPushButton#ListItem:pressed {
    background-color: $SURFACE;
}
QPushButton#ListItem:focus {
    border: 1px solid $ACCENT;
}

/* --------------------------------------------------- entradas de alerta */
QLabel#AlertItem {
    background-color: $DANGER_SOFT;
    border: 1px solid $DANGER_BORDER;
    border-radius: $RADIUS_SM;
    color: $DANGER_TEXT_STRONG;
    font-size: ${FS_SM}px;
    font-weight: $FW_MEDIUM;
    padding: 6px 8px;
}
QLabel#AlertItem[tone="info"] {
    background-color: $INFO_SOFT;
    border: 1px solid $INFO_BORDER;
    color: $INFO_TEXT;
}
QLabel#AlertItem[tone="warning"] {
    background-color: $WARNING_SOFT;
    border: 1px solid $WARNING_BORDER;
    color: $WARNING_TEXT;
}

/* ---------------------------------------------------------- estado vacío */
QLabel#EmptyTitle {
    color: $TEXT_MUTED;
    font-size: ${FS_BASE}px;
    font-weight: $FW_SEMIBOLD;
}
QLabel#EmptyHint {
    color: $TEXT_DIM;
    font-size: ${FS_SM}px;
}

/* --------------------------------------------------- barras de desplazamiento */
QScrollArea {
    background: transparent;
    border: none;
}
QScrollArea > QWidget > QWidget {
    background: transparent;
}
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: $SURFACE_3;
    min-height: 28px;
    border-radius: $RADIUS_SM;
}
QScrollBar::handle:vertical:hover {
    background: $BORDER_STRONG;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
    background: none;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: none;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 2px;
}
QScrollBar::handle:horizontal {
    background: $SURFACE_3;
    min-width: 28px;
    border-radius: $RADIUS_SM;
}
QScrollBar::handle:horizontal:hover {
    background: $BORDER_STRONG;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
    background: none;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: none;
}
"""
)


_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def _asset_url(filename: str) -> str:
    """Ruta absoluta con barras normales para una referencia ``url(...)``."""
    return os.path.join(_ASSETS_DIR, filename).replace("\\", "/")


def build_stylesheet() -> str:
    """Devuelve la hoja de estilos Qt expandida desde los tokens."""
    tokens = dict(TOKENS)
    tokens["DOWN_ARROW"] = _asset_url("chevron-down.svg")
    return _STYLESHEET.substitute(tokens)


def refresh_style(widget) -> None:
    """Reevalúa los selectores de propiedades dinámicas tras un cambio."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_dynamic_property(widget, name: str, value) -> None:
    """Asigna una propiedad dinámica de Qt y reestiliza el widget."""
    widget.setProperty(name, value)
    refresh_style(widget)
