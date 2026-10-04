"""Estabilización temporal de detecciones, espejo del `ToastTracker` de referencia.

El detector decide la clase de una caja frame a frame con un `argmax`/umbral sin
memoria: cuando dos clases están cerca en confianza (p. ej. TCQ y TCOK sobre la
misma tostada) la etiqueta alterna aunque la caja sea la misma. Eso hace que el
operador no pueda leer el estado y que la persistencia guarde decisiones
inconsistentes.

La solución copia la semántica del `ToastTracker` de la referencia:

- Asociación geométrica entre frames: una pista se empareja con la detección de
  mayor score, por IoU (con bonus) o, si el IoU no alcanza, por cercanía de
  centros. El matching es codicioso y una a una.
- Confirmación de "quemada" recién tras `_FRAMES_CONFIRMAR_QUEMADA` frames
  acumulados con una etiqueta quemada; una detección OK no corta la racha.
- Una vez quemada, la pista NO vuelve a OK (latch anti-parpadeo).
- La pista sigue reportándose unos frames perdidos antes de desaparecer.

Además, el estabilizador emite eventos de ciclo de vida por pista (`alta`,
`quemada`, `cambio`, `baja`) para poder contar productos a posteriori. Contar es
agrupar los `baja` por su `label` final.

Toda la clase vive en un único hilo (el lazo de inferencia del pipeline): no es
segura para uso concurrente y no necesita lock.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from backend.inference import ResultadoDeteccion

# IoU mínimo para asociar una detección con una pista por solapamiento.
_IOU_MIN = 0.30
# Distancia máxima (px) entre centros para asociarlas cuando el IoU no alcanza.
_DISTANCIA_MAX_PX = 150.0
# Ventaja de score de una coincidencia por IoU sobre una por distancia.
_BONUS_IOU = 1.0
# Frames que una pista perdida sigue reportándose como visible (anti-parpadeo).
_FRAMES_VISIBLE_PERDIDA = 3
# Frames perdidos tras los cuales la pista se elimina (tolerancia a oclusiones).
_FRAMES_MAX_PERDIDA = 25
# Frames acumulados con etiqueta quemada para confirmar el estado "burnt".
_FRAMES_CONFIRMAR_QUEMADA = 3
# Frames mínimos de vida que debe tener una pista para confirmar el cruce (anti-ruido).
_FRAMES_MINIMOS_CRUCE = 3


def _es_quemada(label: str) -> bool:
    """True si la etiqueta representa una tostada quemada."""
    normal = label.strip().lower()
    return "quemada" in normal or normal == "tcq"


@dataclass(frozen=True, slots=True)
class EventoPista:
    """Evento de ciclo de vida de una pista, para contar productos a posteriori.

    ``tipo`` es ``"alta" | "cruce" | "quemada" | "cambio" | "baja"``. Contar
    productos con línea virtual es registrar los eventos ``cruce`` (o los
    ``baja`` como fallback si la línea no está habilitada); ``quemada`` marca la
    transición irreversible y ``cambio`` las transiciones entre clases no
    quemadas (p. ej. crudo → ok).
    """

    tipo: str
    pista_id: int
    label: str
    confianza: float
    bbox: tuple[int, int, int, int]


@dataclass(slots=True)
class _Pista:
    """Estado temporal de una caja: id, clase, racha de quemado, pérdida y cruce."""

    id: int
    bbox: tuple[int, int, int, int]
    label: str
    confianza: float
    centro_y_prev: float
    centro_y: float
    cruzada: bool = False
    frames_vistos: int = 1
    estado: str = "ok"
    racha_quemada: int = 0
    frames_desde_visto: int = 0


class EstabilizadorDetecciones:
    """Suaviza las detecciones crudas asociándolas entre frames.

    Cada pista se empareja con la detección de mayor score (IoU con bonus o
    cercanía de centros) y conserva su etiqueta hasta confirmar "quemada" por
    `_FRAMES_CONFIRMAR_QUEMADA` frames acumulados. La quemada es irreversible
    en la vida de la pista, así que la clase no parpadea aunque el detector dude.
    Si se configura `linea_conteo_y`, emite un evento ``cruce`` exactamente una
    vez cuando el centroide atraviesa la línea hacia abajo.
    """

    def __init__(self, linea_conteo_y: int | None = None) -> None:
        self._linea_y = linea_conteo_y
        self._pistas: list[_Pista] = []
        self._siguiente_id = 1
        self._eventos: list[EventoPista] = []

    @property
    def linea_y(self) -> int | None:
        """Coordenada Y de la línea virtual de conteo, o None si está deshabilitada."""
        return self._linea_y

    @linea_y.setter
    def linea_y(self, valor: int | None) -> None:
        self._linea_y = valor

    def estabilizar(
        self, detecciones: Sequence[ResultadoDeteccion]
    ) -> list[ResultadoDeteccion]:
        """Asocia las detecciones con las pistas y devuelve las pistas visibles.

        Genera las candidatas (score, pista, detección), las resuelve con
        matching codicioso y actualiza el estado. Las detecciones sin pareja
        crean pista nueva (el NMS class-agnostic ya evita solapadas de distinta
        clase). El orden de salida es el de las pistas.
        """
        crudas = list(detecciones)

        candidatas: list[tuple[float, int, int]] = []
        for indice_pista, pista in enumerate(self._pistas):
            for indice_deteccion, deteccion in enumerate(crudas):
                iou = self._iou(pista.bbox, deteccion.bbox)
                if iou >= _IOU_MIN:
                    candidatas.append((_BONUS_IOU + iou, indice_pista, indice_deteccion))
                else:
                    distancia = self._distancia(pista.bbox, deteccion.bbox)
                    if distancia < _DISTANCIA_MAX_PX:
                        score = 1.0 - distancia / _DISTANCIA_MAX_PX
                        candidatas.append((score, indice_pista, indice_deteccion))
        candidatas.sort(key=lambda candidata: candidata[0], reverse=True)

        pistas_asignadas: set[int] = set()
        detecciones_usadas: set[int] = set()
        for _score, indice_pista, indice_deteccion in candidatas:
            if indice_pista in pistas_asignadas or indice_deteccion in detecciones_usadas:
                continue
            pistas_asignadas.add(indice_pista)
            detecciones_usadas.add(indice_deteccion)
            self._actualizar(self._pistas[indice_pista], crudas[indice_deteccion])

        for indice_pista, pista in enumerate(self._pistas):
            if indice_pista not in pistas_asignadas:
                pista.frames_desde_visto += 1

        for indice_deteccion, deteccion in enumerate(crudas):
            if indice_deteccion in detecciones_usadas:
                continue
            nuevo_centro_y = deteccion.bbox[1] + deteccion.bbox[3] / 2.0
            # Si nace ya por debajo de la línea, se inicializa como cruzada para
            # no duplicar conteo si fue una fragmentación tardía.
            ya_paso = self._linea_y is not None and nuevo_centro_y >= self._linea_y
            pista = _Pista(
                id=self._siguiente_id,
                bbox=deteccion.bbox,
                label=deteccion.label,
                confianza=deteccion.confianza,
                centro_y_prev=nuevo_centro_y,
                centro_y=nuevo_centro_y,
                cruzada=ya_paso,
                frames_vistos=1,
                racha_quemada=1 if _es_quemada(deteccion.label) else 0,
            )
            self._siguiente_id += 1
            self._confirmar_quemada(pista)
            self._eventos.append(
                EventoPista("alta", pista.id, pista.label, pista.confianza, pista.bbox)
            )
            self._pistas.append(pista)

        vivas: list[_Pista] = []
        for pista in self._pistas:
            if pista.frames_desde_visto > _FRAMES_MAX_PERDIDA:
                self._eventos.append(
                    EventoPista("baja", pista.id, pista.label, pista.confianza, pista.bbox)
                )
            else:
                vivas.append(pista)
        self._pistas = vivas

        return [
            ResultadoDeteccion(
                label=pista.label,
                confianza=pista.confianza,
                bbox=pista.bbox,
            )
            for pista in self._pistas
            if pista.frames_desde_visto <= _FRAMES_VISIBLE_PERDIDA
        ]

    def eventos(self) -> list[EventoPista]:
        """Devuelve los eventos pendientes y limpia la lista interna."""
        pendientes = self._eventos
        self._eventos = []
        return pendientes

    def reiniciar(self) -> None:
        """Emite ``baja`` por cada pista viva y vacía el estado.

        Se llama al cambiar de modelo: las pistas que estaban en escena se
        cierran para que el conteo a posteriori no quede con productos abiertos.
        Los eventos quedan pendientes para el próximo `eventos()`.
        """
        for pista in self._pistas:
            self._eventos.append(
                EventoPista("baja", pista.id, pista.label, pista.confianza, pista.bbox)
            )
        self._pistas.clear()
        self._siguiente_id = 1

    def _actualizar(self, pista: _Pista, deteccion: ResultadoDeteccion) -> None:
        """Aplica una detección emparejada, actualiza la racha y emite eventos."""
        pista.bbox = deteccion.bbox
        pista.confianza = deteccion.confianza
        pista.frames_desde_visto = 0
        pista.frames_vistos += 1

        nuevo_centro_y = deteccion.bbox[1] + deteccion.bbox[3] / 2.0
        pista.centro_y_prev = pista.centro_y
        pista.centro_y = nuevo_centro_y

        # Chequear cruce de línea virtual hacia abajo
        if (
            self._linea_y is not None
            and not pista.cruzada
            and pista.frames_vistos >= _FRAMES_MINIMOS_CRUCE
            and pista.centro_y_prev < self._linea_y <= pista.centro_y
        ):
            pista.cruzada = True
            self._eventos.append(
                EventoPista("cruce", pista.id, pista.label, pista.confianza, pista.bbox)
            )

        if pista.estado == "burnt":
            # Una tostada quemada no se des-quema: la etiqueta queda latcheada.
            return

        label_previo = pista.label

        if not _es_quemada(deteccion.label):
            # Una detección OK NO corta la racha: es acumulativa durante la vida
            # de la pista (igual que el tracker de referencia), así una NPU que
            # duda entre TCQ/TCOK no impide confirmar la quemada.
            pista.label = deteccion.label
            # Sólo las transiciones entre clases NO quemadas cuentan como cambio.
            if not _es_quemada(label_previo) and deteccion.label != label_previo:
                self._eventos.append(
                    EventoPista(
                        "cambio", pista.id, pista.label, pista.confianza, pista.bbox
                    )
                )
            return

        # Etiqueta quemada: puede ser transitoria. No se pisa la etiqueta previa
        # ni se emite nada hasta confirmar; así el `baja` nunca cierra como
        # quemado un producto que nunca se confirmó.
        pista.racha_quemada += 1
        if self._confirmar_quemada(pista):
            pista.label = deteccion.label
            self._eventos.append(
                EventoPista("quemada", pista.id, pista.label, pista.confianza, pista.bbox)
            )

    @staticmethod
    def _confirmar_quemada(pista: _Pista) -> bool:
        """Marca la pista como quemada si la racha llegó al umbral.

        Devuelve True recién en la transición a "burnt", para que el evento
        ``quemada`` se emita una sola vez. La etiqueta ya es la cruda; sólo el
        estado cambia, y es irreversible mientras viva la pista.
        """
        if pista.estado != "burnt" and pista.racha_quemada >= _FRAMES_CONFIRMAR_QUEMADA:
            pista.estado = "burnt"
            return True
        return False

    @staticmethod
    def _iou(
        caja_a: tuple[int, int, int, int], caja_b: tuple[int, int, int, int]
    ) -> float:
        """Intersección sobre unión de dos cajas ``(left, top, width, height)``."""
        ax, ay, ancho_a, alto_a = caja_a
        bx, by, ancho_b, alto_b = caja_b

        izquierda = max(ax, bx)
        arriba = max(ay, by)
        derecha = min(ax + ancho_a, bx + ancho_b)
        abajo = min(ay + alto_a, by + alto_b)

        ancho = max(0, derecha - izquierda)
        alto = max(0, abajo - arriba)
        interseccion = ancho * alto
        if interseccion <= 0:
            return 0.0

        union = ancho_a * alto_a + ancho_b * alto_b - interseccion
        if union <= 0:
            return 0.0
        return interseccion / union

    @staticmethod
    def _distancia(
        caja_a: tuple[int, int, int, int], caja_b: tuple[int, int, int, int]
    ) -> float:
        """Distancia euclidiana entre los centros de dos cajas."""
        centro_a_x = caja_a[0] + caja_a[2] / 2
        centro_a_y = caja_a[1] + caja_a[3] / 2
        centro_b_x = caja_b[0] + caja_b[2] / 2
        centro_b_y = caja_b[1] + caja_b[3] / 2
        return ((centro_a_x - centro_b_x) ** 2 + (centro_a_y - centro_b_y) ** 2) ** 0.5
