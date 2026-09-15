import os


def resolve_path(relative_path):
    """
    Dada una ruta relativa, intenta resolverla a una ruta absoluta válida.
    La función busca la ruta en varios lugares:
    1. Comprobar si la ruta existe tal cual relativa al directorio de trabajo actual (CWD).
    2. Comprobar si la ruta existe relativa al directorio raíz del proyecto (padre del directorio frontend/).
    3. Normalizar separadores y probar con o sin el prefijo "yolov11-python/".
    4. Mapeo específico para diferentes distribuciones de carpetas, buscando en CWD y en el directorio raíz del proyecto.
    Si no se encuentra ninguna coincidencia, devuelve la ruta relativa original.
    """
    if not relative_path:
        return relative_path

    # Obtener el directorio raíz del proyecto (padre del directorio frontend/)
    root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    # Si es una ruta absoluta y existe, devolverla directamente
    if os.path.isabs(relative_path) and os.path.exists(relative_path):
        return relative_path

    # 1. Comprobar si existe tal cual relativa al CWD
    if os.path.exists(relative_path):
        return os.path.abspath(relative_path)

    # 2. Comprobar si existe relativa a root_dir
    path_in_root = os.path.join(root_dir, relative_path)
    if os.path.exists(path_in_root):
        return path_in_root

    # Normalizar separadores
    normalized = relative_path.replace("\\", "/")

    # Si empieza con yolov11-python/, probar a quitar el prefijo
    if normalized.startswith("yolov11-python/"):
        stripped = normalized[len("yolov11-python/"):]
        # Buscar en CWD
        if os.path.exists(stripped):
            return os.path.abspath(stripped)
        # Buscar en root_dir
        path_stripped_in_root = os.path.join(root_dir, stripped)
        if os.path.exists(path_stripped_in_root):
            return path_stripped_in_root

    # Si no empieza con yolov11-python/, probar a añadir el prefijo
    else:
        prefixed = f"yolov11-python/{normalized}"
        if os.path.exists(prefixed):
            return os.path.abspath(prefixed)
        path_prefixed_in_root = os.path.join(root_dir, prefixed)
        if os.path.exists(path_prefixed_in_root):
            return path_prefixed_in_root

    # Mapeo específico para diferentes distribuciones de carpetas
    mapping = {
        "yolov11-python/yolo11n.onnx": "ai_training/models/yolo11n.onnx",
        "yolov11-python/tostadas_v1.onnx": "ai_training/models/tostadas_v1.onnx",
        "yolov11-python/tostadas_v1.names": "ai_training/models/tostadas_v1.names",
        "yolov11-python/tostadas_v2.onnx": "ai_training/models/tostadas_v2.onnx",
        "yolov11-python/tostadas_v2.names": "ai_training/models/tostadas_v2.names",
        "yolov11-python/data/class.names": "ai_training/models/class.names",
        "yolov11-python/data/videos/road.mp4": "multimedia/videos/road.mp4",
        "yolov11-python/data/videos": "multimedia/videos"
    }
    
    # Probar mapeos
    for key, val in mapping.items():
        if normalized == key or normalized == key.replace("yolov11-python/", ""):
            # 1. Comprobar val relativo a CWD
            if os.path.exists(val):
                return os.path.abspath(val)
            # 2. Comprobar val relativo a root_dir
            path_val_in_root = os.path.join(root_dir, val)
            if os.path.exists(path_val_in_root):
                return path_val_in_root
            # 3. Comprobar con prefijo yolov11-python/ en CWD
            prefixed_val = f"yolov11-python/{val}"
            if os.path.exists(prefixed_val):
                return os.path.abspath(prefixed_val)
            # 4. Comprobar con prefijo en root_dir
            path_prefixed_val_in_root = os.path.join(root_dir, prefixed_val)
            if os.path.exists(path_prefixed_val_in_root):
                return path_prefixed_val_in_root

    return relative_path
