import { useCallback, useEffect, useRef, useState } from 'react'

/*
 * Pilotage de la lampe torche (flash arrière) du téléphone via l'API MediaStream.
 * On ouvre un flux caméra arrière puis on active la contrainte `torch`.
 *
 * Limites connues : la contrainte `torch` n'existe que dans un contexte sécurisé
 * (HTTPS) et là où le navigateur la supporte (Android/Chrome notamment). iOS /
 * Safari NE la supporte PAS — `toggle()` renvoie alors `false`, à charge de
 * l'appelant d'avertir l'utilisateur.
 */

// La contrainte/capacité `torch` ne figure pas encore dans les types DOM standard.
interface TorchCapabilities extends MediaTrackCapabilities {
  torch?: boolean
}
interface TorchConstraintSet extends MediaTrackConstraintSet {
  torch?: ConstrainBoolean
}

export function useTorch() {
  const trackRef = useRef<MediaStreamTrack | null>(null)
  const [on, setOn] = useState(false)

  const stop = useCallback(() => {
    trackRef.current?.stop()
    trackRef.current = null
    setOn(false)
  }, [])

  /**
   * Bascule la lampe torche. Renvoie `false` si l'allumage a échoué
   * (caméra refusée, contrainte non supportée…). L'extinction réussit toujours.
   */
  const toggle = useCallback(async (): Promise<boolean> => {
    // Déjà allumée → on éteint et on relâche la caméra.
    if (trackRef.current) {
      try {
        await trackRef.current.applyConstraints({ advanced: [{ torch: false }] as TorchConstraintSet[] })
      } catch {
        // L'extinction physique peut échouer ; on coupe le flux dans tous les cas.
      }
      stop()
      return true
    }

    if (!navigator.mediaDevices?.getUserMedia) return false

    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } })
    } catch {
      return false
    }

    const track = stream.getVideoTracks()[0]
    const caps = track?.getCapabilities?.() as TorchCapabilities | undefined
    if (!track || !caps?.torch) {
      stream.getTracks().forEach((t) => t.stop())
      return false
    }

    try {
      await track.applyConstraints({ advanced: [{ torch: true }] as TorchConstraintSet[] })
    } catch {
      stream.getTracks().forEach((t) => t.stop())
      return false
    }

    trackRef.current = track
    setOn(true)
    return true
  }, [stop])

  // Sécurité : coupe la torche (et la caméra) au démontage.
  useEffect(() => stop, [stop])

  return { on, toggle }
}
