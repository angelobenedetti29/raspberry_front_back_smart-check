from dataclasses import dataclass
from typing import Tuple, List, Dict

from backend.domain.entities.detection import is_burnt

# Máxima distancia (px) entre centros para seguir considerando que dos cajas son
# la misma tostada cuando el IoU no alcanza el umbral.
MAX_MATCH_DISTANCE_PX = 150.0
# Ventaja de score de una coincidencia por IoU sobre una por distancia de centros.
IOU_SCORE_BONUS = 1.0
# Frames que una tostada perdida sigue reportándose como visible (evita parpadeo).
VISIBLE_LOST_FRAMES = 3


@dataclass
class TrackedToast:
    id: int
    bbox: Tuple[int, int, int, int]  # (x, y, w, h)
    label: str
    confidence: float
    state: str  # "ok" o "burnt"
    frames_since_seen: int = 0
    consecutive_burnt_frames: int = 0
    alert_triggered: bool = False


class ToastTracker:
    def __init__(self, iou_threshold: float = 0.3, max_lost_frames: int = 10, min_burnt_confirm_frames: int = 3):
        self.iou_threshold = iou_threshold
        self.max_lost_frames = max_lost_frames
        self.min_burnt_confirm_frames = min_burnt_confirm_frames
        self.tracked_toasts: Dict[int, TrackedToast] = {}
        self.next_id = 1

    def reset(self):
        """Reinicia el estado del tracker por completo."""
        self.tracked_toasts.clear()
        self.next_id = 1

    # ---------------------------------------------------------------- geometría
    def _calculate_iou(self, boxA: Tuple[int, int, int, int], boxB: Tuple[int, int, int, int]) -> float:
        """Calcula el Intersection over Union (IoU) entre dos cajas delimitadoras."""
        xA = max(boxA[0], boxB[0])
        yA = max(boxA[1], boxB[1])
        xB = min(boxA[0] + boxA[2], boxB[0] + boxB[2])
        yB = min(boxA[1] + boxA[3], boxB[1] + boxB[3])

        interArea = max(0, xB - xA) * max(0, yB - yA)
        boxAArea = boxA[2] * boxA[3]
        boxBArea = boxB[2] * boxB[3]

        unionArea = boxAArea + boxBArea - interArea
        if unionArea <= 0:
            return 0.0
        return interArea / unionArea

    def _calculate_distance(self, boxA: Tuple[int, int, int, int], boxB: Tuple[int, int, int, int]) -> float:
        """Calcula la distancia euclidiana entre los centros de dos cajas."""
        cA_x = boxA[0] + boxA[2] / 2
        cA_y = boxA[1] + boxA[3] / 2
        cB_x = boxB[0] + boxB[2] / 2
        cB_y = boxB[1] + boxB[3] / 2
        return ((cA_x - cB_x) ** 2 + (cA_y - cB_y) ** 2) ** 0.5

    # ------------------------------------------------------------------ estados
    def _update_burnt_state(self, tracked_toast: TrackedToast) -> None:
        """Confirma o mantiene el estado de quemada de una tostada.

        Una tostada quemada no se des-quema. El resto pasa a "burnt" al acumular
        ``min_burnt_confirm_frames`` frames *consecutivos* con detección de
        quemada; una detección OK reinicia el contador.
        """
        if tracked_toast.state == "burnt":
            tracked_toast.label = "TCQ"
            return

        threshold = max(1, self.min_burnt_confirm_frames)
        if tracked_toast.consecutive_burnt_frames >= threshold:
            tracked_toast.state = "burnt"
            tracked_toast.label = "TCQ"
        else:
            tracked_toast.label = "TCOK"

    def _apply_match(self, tracked_toast: TrackedToast, det) -> None:
        """Aplica una detección emparejada y actualiza su contador de quemado."""
        tracked_toast.bbox = det.bbox
        tracked_toast.confidence = det.confidence
        tracked_toast.frames_since_seen = 0

        if is_burnt(det.label):
            tracked_toast.consecutive_burnt_frames += 1
        else:
            # Frames consecutivos: una detección OK corta la racha.
            tracked_toast.consecutive_burnt_frames = 0

        self._update_burnt_state(tracked_toast)

    # ------------------------------------------------------------------ matching
    def _find_matches(self, new_detections, tracked_ids) -> List[Tuple[float, int, int]]:
        """Candidatas (score, track_id, det_idx) ordenadas por score descendente.

        Una coincidencia por IoU tiene prioridad sobre una por distancia de
        centros (que solo aplica por debajo de ``MAX_MATCH_DISTANCE_PX``).
        """
        matches: List[Tuple[float, int, int]] = []
        for t_id in tracked_ids:
            tracked_toast = self.tracked_toasts[t_id]
            for det_idx, det in enumerate(new_detections):
                iou = self._calculate_iou(tracked_toast.bbox, det.bbox)
                if iou >= self.iou_threshold:
                    matches.append((IOU_SCORE_BONUS + iou, t_id, det_idx))
                else:
                    dist = self._calculate_distance(tracked_toast.bbox, det.bbox)
                    if dist < MAX_MATCH_DISTANCE_PX:
                        matches.append((1.0 - dist / MAX_MATCH_DISTANCE_PX, t_id, det_idx))

        matches.sort(key=lambda match: match[0], reverse=True)
        return matches

    def _apply_matches(self, matches, new_detections) -> Tuple[set, set]:
        """Emparejamiento codicioso (greedy matching) sobre las candidatas.

        Devuelve los ids de track y los índices de detección ya emparejados.
        """
        matched_track_ids: set = set()
        matched_det_indices: set = set()

        for _score, t_id, det_idx in matches:
            if t_id in matched_track_ids or det_idx in matched_det_indices:
                continue

            matched_track_ids.add(t_id)
            matched_det_indices.add(det_idx)
            self._apply_match(self.tracked_toasts[t_id], new_detections[det_idx])

        return matched_track_ids, matched_det_indices

    # ------------------------------------------------------------------- fases
    def _mark_unmatched_as_lost(self, tracked_ids, matched_track_ids) -> None:
        """Suma un frame perdido a cada tostada que no se volvió a ver."""
        for t_id in tracked_ids:
            if t_id not in matched_track_ids:
                self.tracked_toasts[t_id].frames_since_seen += 1

    def _track_new_detections(self, new_detections, matched_det_indices) -> None:
        """Registra como nuevas tostadas las detecciones sin emparejar."""
        for det_idx, det in enumerate(new_detections):
            if det_idx in matched_det_indices:
                continue

            # Un track nuevo entra como "ok" y pasa por la misma confirmación
            # por frames consecutivos que los tracks existentes.
            new_toast = TrackedToast(
                id=self.next_id,
                bbox=det.bbox,
                label="TCOK",
                confidence=det.confidence,
                state="ok",
                consecutive_burnt_frames=1 if is_burnt(det.label) else 0,
            )
            self.tracked_toasts[self.next_id] = new_toast
            self.next_id += 1
            self._update_burnt_state(new_toast)

    def _prune_lost(self) -> None:
        """Elimina las tostadas perdidas por demasiado tiempo."""
        to_delete = [
            t_id
            for t_id, tracked_toast in self.tracked_toasts.items()
            if tracked_toast.frames_since_seen > self.max_lost_frames
        ]
        for t_id in to_delete:
            del self.tracked_toasts[t_id]

        # Reinicia el contador si la escena queda completamente limpia.
        if not self.tracked_toasts:
            self.next_id = 1

    def _collect_results(self) -> Tuple[List[TrackedToast], List[TrackedToast]]:
        """Devuelve las tostadas visibles y las que acaban de confirmarse quemadas."""
        active_toasts: List[TrackedToast] = []
        newly_burnt_toasts: List[TrackedToast] = []

        for tracked_toast in self.tracked_toasts.values():
            # Se muestra la tostada aunque se haya perdido unos frames (anti-parpadeo).
            if tracked_toast.frames_since_seen <= VISIBLE_LOST_FRAMES:
                active_toasts.append(tracked_toast)

            # Requiere alarma la tostada que recién pasó a quemada.
            if tracked_toast.state == "burnt" and not tracked_toast.alert_triggered:
                tracked_toast.alert_triggered = True
                newly_burnt_toasts.append(tracked_toast)

        return active_toasts, newly_burnt_toasts

    # ------------------------------------------------------------------ pública
    def update(self, detections) -> Tuple[List[TrackedToast], List[TrackedToast]]:
        """
        Actualiza el tracker con las nuevas detecciones del fotograma.
        Retorna:
            - active_toasts: Lista de todas las tostadas actualmente visibles.
            - newly_burnt_toasts: Lista de tostadas que acaban de transicionar a quemadas en este fotograma.
        """
        new_detections = list(detections)
        tracked_ids = list(self.tracked_toasts.keys())

        matches = self._find_matches(new_detections, tracked_ids)
        matched_track_ids, matched_det_indices = self._apply_matches(matches, new_detections)

        self._mark_unmatched_as_lost(tracked_ids, matched_track_ids)
        self._track_new_detections(new_detections, matched_det_indices)
        self._prune_lost()

        return self._collect_results()
