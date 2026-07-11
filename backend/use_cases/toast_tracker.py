from dataclasses import dataclass
from typing import Tuple, List, Dict

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
    def __init__(self, iou_threshold: float = 0.3, max_lost_frames: int = 10, min_burnt_confirm_frames: int = 5):
        self.iou_threshold = iou_threshold
        self.max_lost_frames = max_lost_frames
        self.min_burnt_confirm_frames = min_burnt_confirm_frames
        self.tracked_toasts: Dict[int, TrackedToast] = {}
        self.next_id = 1

    def reset(self):
        """Reinicia el estado del tracker por completo."""
        self.tracked_toasts.clear()
        self.next_id = 1

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

    def update(self, detections) -> Tuple[List[TrackedToast], List[TrackedToast]]:
        """
        Actualiza el tracker con las nuevas detecciones del fotograma.
        Retorna:
            - active_toasts: Lista de todas las tostadas actualmente visibles.
            - newly_burnt_toasts: Lista de tostadas que acaban de transicionar a quemadas en este fotograma.
        """
        new_detections = list(detections)
        tracked_ids = list(self.tracked_toasts.keys())

        # 1. Calcular coincidencia entre las tostadas trackeadas y las nuevas detecciones
        matches = []
        for t_id in tracked_ids:
            tracked_toast = self.tracked_toasts[t_id]
            for det_idx, det in enumerate(new_detections):
                iou = self._calculate_iou(tracked_toast.bbox, det.bbox)
                if iou >= self.iou_threshold:
                    # Prioridad alta para coincidencia por IoU
                    matches.append((1.0 + iou, t_id, det_idx))
                else:
                    # Fallback a distancia de centros si no hay suficiente overlap
                    dist = self._calculate_distance(tracked_toast.bbox, det.bbox)
                    if dist < 150.0:  # Máxima distancia permitida de 150px
                        score = 1.0 - (dist / 150.0)
                        matches.append((score, t_id, det_idx))

        # Ordenar coincidencias por score de manera descendente
        matches.sort(key=lambda x: x[0], reverse=True)

        matched_track_ids = set()
        matched_det_indices = set()

        # 2. Emparejamiento codicioso (Greedy Matching)
        for score, t_id, det_idx in matches:
            if t_id in matched_track_ids or det_idx in matched_det_indices:
                continue

            matched_track_ids.add(t_id)
            matched_det_indices.add(det_idx)

            # Actualizar tostada existente
            tracked_toast = self.tracked_toasts[t_id]
            det = new_detections[det_idx]
            tracked_toast.bbox = det.bbox
            tracked_toast.confidence = det.confidence
            tracked_toast.frames_since_seen = 0

            # Lógica de máquina de estados de tostada
            is_burnt_detection = "quemada" in det.label.lower() or det.label.lower() == "tcq"
            
            if tracked_toast.state == "burnt":
                # La tostada quemada no puede des-quemarse
                tracked_toast.label = "TCQ"
            else:
                if is_burnt_detection:
                    tracked_toast.consecutive_burnt_frames += 1

                # Transición a quemado si se alcanza el umbral de confirmación (3 frames en total)
                if tracked_toast.consecutive_burnt_frames >= 3:
                    tracked_toast.state = "burnt"
                    tracked_toast.label = "TCQ"
                else:
                    tracked_toast.label = "TCOK"

        # 3. Manejo de tostadas bajo seguimiento no emparejadas (perdidas en este fotograma)
        for t_id in tracked_ids:
            if t_id not in matched_track_ids:
                tracked_toast = self.tracked_toasts[t_id]
                tracked_toast.frames_since_seen += 1

        # 4. Manejo de nuevas detecciones no emparejadas (nuevas tostadas que entran)
        for det_idx, det in enumerate(new_detections):
            if det_idx not in matched_det_indices:
                is_burnt_detection = "quemada" in det.label.lower() or det.label.lower() == "tcq"
                
                # Inicialmente asumimos estado "ok" y dejamos que la histéresis confirme si está quemada
                # A menos que min_burnt_confirm_frames sea 0 o 1
                initial_state = "ok"
                consecutive_burnt = 1 if is_burnt_detection else 0
                
                if is_burnt_detection and self.min_burnt_confirm_frames <= 1:
                    initial_state = "burnt"

                initial_label = "TCQ" if initial_state == "burnt" else "TCOK"

                new_toast = TrackedToast(
                    id=self.next_id,
                    bbox=det.bbox,
                    label=initial_label,
                    confidence=det.confidence,
                    state=initial_state,
                    consecutive_burnt_frames=consecutive_burnt
                )
                self.tracked_toasts[self.next_id] = new_toast
                self.next_id += 1

        # 5. Limpieza de tostadas perdidas por demasiado tiempo
        to_delete = [t_id for t_id, t in self.tracked_toasts.items() if t.frames_since_seen > self.max_lost_frames]
        for t_id in to_delete:
            del self.tracked_toasts[t_id]

        # OPCIÓN B: Reiniciar el contador si la escena queda completamente limpia de tostadas
        if not self.tracked_toasts:
            self.next_id = 1

        # 6. Recopilar resultados activos y detectar quién requiere disparar alarma
        active_toasts = []
        newly_burnt_toasts = []

        for t_id, tracked_toast in self.tracked_toasts.items():
            # Permitir mostrar la tostada incluso si se perdió por hasta 3 fotogramas (evita parpadeo de desaparición)
            if tracked_toast.frames_since_seen <= 3:
                active_toasts.append(tracked_toast)
                
            # Identificar si acaba de pasar a quemado y requiere disparar la alarma
            if tracked_toast.state == "burnt" and not tracked_toast.alert_triggered:
                tracked_toast.alert_triggered = True
                newly_burnt_toasts.append(tracked_toast)

        return active_toasts, newly_burnt_toasts
