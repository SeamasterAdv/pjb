import os
import time
import logging
from collections import deque
from typing import Callable, Dict, List, Optional, Tuple

import cv2
import mediapipe as mp
import numpy as np

logger = logging.getLogger(__name__)


def detectar_camera_disponivel(indices=None):
    """Procura automaticamente uma câmera disponível em vários índices do sistema."""
    if indices is None:
        indices = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

    for camera_index in indices:
        try:
            cap = cv2.VideoCapture(camera_index)
            if cap.isOpened():
                ok, _ = cap.read()
                cap.release()
                if ok:
                    return camera_index
        except Exception:
            continue

    try:
        cap = cv2.VideoCapture(cv2.CAP_DSHOW)
        if cap.isOpened():
            ok, _ = cap.read()
            cap.release()
            if ok:
                return 0
    except Exception:
        pass

    return None


def webcam_disponivel(camera_index: int = None) -> bool:
    """Retorna True quando alguma webcam consegue abrir sem falha."""
    if camera_index is not None:
        try:
            cap = cv2.VideoCapture(camera_index)
            ok = cap.isOpened()
            cap.release()
            return ok
        except Exception:
            return False
    return detectar_camera_disponivel() is not None


class GestosMaoDetector:
    """Detecta gestos da mão e devolve o gesto mais provável.

    Gesta por padrão:
    - mao_aberta
    - indicador
    - dois_dedos
    - punho
    - fechar_mao
    - outro
    """

    def __init__(self, camera_index: int = 0, max_hands: int = 1, min_confidence: float = 0.7):
        self.camera_index = camera_index
        self.max_hands = max_hands
        self.min_confidence = min_confidence

        self.mp_hands = mp.solutions.hands
        self.hands = self.mp_hands.Hands(
            max_num_hands=max_hands,
            min_detection_confidence=min_confidence,
            min_tracking_confidence=min_confidence,
        )
        self.mp_draw = mp.solutions.drawing_utils

    def _landmark_to_np(self, landmark) -> np.ndarray:
        return np.array([landmark.x, landmark.y, landmark.z], dtype=float)

    def _dedos_levantados(self, landmarks) -> List[bool]:
        """Retorna lista booleana indicando quais dedos estão levantados."""
        if not landmarks or len(landmarks) < 21:
            return [False] * 5

        # Índices relevantes: polegar, indicador, médio, anelar, mindinho
        pontos = {
            "polegar": landmarks[4],
            "indicador": landmarks[8],
            "medio": landmarks[12],
            "anelar": landmarks[16],
            "mindinho": landmarks[20],
        }

        # Posição base para comparar
        wrist = landmarks[0]
        index_base = landmarks[5]
        middle_base = landmarks[9]
        ring_base = landmarks[13]
        pinky_base = landmarks[17]

        dedos = [
            pontos["polegar"].x > wrist.x and pontos["polegar"].x > landmarks[2].x,
            pontos["indicador"].y < index_base.y,
            pontos["medio"].y < middle_base.y,
            pontos["anelar"].y < ring_base.y,
            pontos["mindinho"].y < pinky_base.y,
        ]
        return dedos

    def _distancia(self, a, b):
        return float(np.linalg.norm(np.array([a.x, a.y, a.z]) - np.array([b.x, b.y, b.z])))

    def classificar_gesto(self, landmarks) -> str:
        if not landmarks or len(landmarks) < 21:
            return "nenhuma_mao"

        dedos = self._dedos_levantados(landmarks)
        indicador_levantado = dedos[1]
        medio_levantado = dedos[2]
        anelar_levantado = dedos[3]
        mindinho_levantado = dedos[4]

        # Fechamento da mão: pouca área e dedos flexionados
        if not any(dedos):
            return "punho"

        # Mão aberta
        if all(dedos):
            return "mao_aberta"

        # Indicador apontando
        if indicador_levantado and not medio_levantado and not anelar_levantado and not mindinho_levantado:
            return "indicador"

        # Dois dedos apontando (indicador + médio)
        if indicador_levantado and medio_levantado and not anelar_levantado and not mindinho_levantado:
            return "dois_dedos"

        # Mão fechada em forma de punho sem dedos levantados
        if sum(dedos) <= 1:
            return "fechar_mao"

        return "outro"

    def processar_frame(self, frame_bgr) -> Tuple[Optional[str], Optional[np.ndarray], Optional[dict]]:
        """Processa uma imagem e retorna gesto + frame + landmarks, se existirem."""
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        results = self.hands.process(rgb)

        gesto = None
        landmarks = None
        dados = None

        if results.multi_hand_landmarks:
            for hand_landmarks in results.multi_hand_landmarks:
                landmarks = hand_landmarks.landmark
                gesto = self.classificar_gesto(landmarks)
                dados = {
                    "gesto": gesto,
                    "qtd_maos": len(results.multi_hand_landmarks),
                    "landmarks": landmarks,
                }
                break

        return gesto, landmarks, dados

    def iniciar_camera(self, exibir: bool = True, callback: Optional[Callable[[str, np.ndarray], None]] = None, max_frames: Optional[int] = None):
        """Executa o loop da webcam até pressionar ESC ou atingir um limite."""
        cap = cv2.VideoCapture(self.camera_index)
        if not cap.isOpened():
            if self.camera_index == 0:
                self.camera_index = detectar_camera_disponivel() or 0
                cap = cv2.VideoCapture(self.camera_index)
            if not cap.isOpened():
                raise RuntimeError(f"Não foi possível abrir a webcam index {self.camera_index}.")

        contador = 0
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    logger.warning("Falha ao capturar frame da webcam.")
                    break

                gesto, landmarks, dados = self.processar_frame(frame)
                if gesto and callback:
                    callback(gesto, frame)

                if exibir:
                    cv2.putText(frame, f"Gesto: {gesto or 'nenhum'}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
                    cv2.imshow("Beatriz - Webcam", frame)

                contador += 1
                if max_frames and contador >= max_frames:
                    break

                if exibir and cv2.waitKey(1) & 0xFF == 27:
                    break

            return True
        finally:
            cap.release()
            if exibir:
                cv2.destroyAllWindows()


class GestorVisao:
    """Conecta os gestos à ação do sistema."""

    def __init__(self, funcoes: Optional[Dict[str, Callable]] = None):
        self.funcoes = funcoes or {}
        self.historial = deque(maxlen=30)
        selfultima_acao = None

    def registrar_acao(self, nome: str, funcao: Callable):
        self.funcoes[nome] = funcao

    def interpretar_gesto(self, gesto: str, contexto: Optional[dict] = None) -> Optional[str]:
        if not gesto:
            return None

        self.historial.append((time.time(), gesto))

        mapa = {
            "mao_aberta": "explorar",
            "indicador": "selecionar",
            "dois_dedos": "pesquisar",
            "punho": "cancelar",
            "fechar_mao": "confirmar",
        }

        acao = mapa.get(gesto, "outro")
        self.ultima_acao = acao

        if acao in self.funcoes:
            try:
                resultado = self.funcoes[acao](contexto or {})
                return str(resultado)
            except Exception as exc:
                logger.exception("Erro ao executar ação visual %s: %s", acao, exc)
                return f"Erro ao executar ação visual: {exc}"

        return acao


def detectar_gesto_em_frame(frame_bgr, detector=None) -> Tuple[Optional[str], Optional[dict]]:
    if detector is None:
        detector = GestosMaoDetector()
    gesto, _, dados = detector.processar_frame(frame_bgr)
    return gesto, dados


if __name__ == "__main__":
    print("Inicializando detector de gestos da mão...")
    detector = GestosMaoDetector()
    gestor = GestorVisao(funcoes={
        "explorar": lambda ctx: "modo explorar ativado",
        "selecionar": lambda ctx: "selecionar objeto",
        "pesquisar": lambda ctx: "iniciar pesquisa",
        "cancelar": lambda ctx: "cancelar ação",
        "confirmar": lambda ctx: "confirmar ação",
    })

    def callback(gesto, frame):
        resposta = gestor.interpretar_gesto(gesto, {"frame": frame})
        if resposta:
            print(f"Gesto: {gesto} -> {resposta}")

    try:
        detector.iniciar_camera(exibir=True, callback=callback, max_frames=100)
    except Exception as exc:
        print(f"Erro ao iniciar webcam: {exc}")
