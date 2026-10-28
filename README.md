# Fitbands

Control de vibración para pulseras **H59MAX** (protocolo Yawell / QWatch Pro) desde una página en la PC. Sirve para una o dos bandas, marcadas como izquierda y derecha.

Chrome no puede reconectar una banda ya permitida sin volver a abrir el selector. Por eso la conexión la hace un servidor local con Python: guarda la dirección de cada lado y la retoma solo.

## Qué hace

- Conecta las bandas que estén libres de la app del teléfono.
- Vibra con pulso, pausa y cantidad de repeticiones. El modo **Inicio y corte** manda el arranque, espera el tiempo del pulso y manda el corte. **Toque corto** repite el zumbido de una notificación; la duración de cada toque la fija la banda.
- Muestra la batería de cada lado.
- Al conectar apaga el pulso automático, el oxígeno, el estrés y la variabilidad cardíaca, y corta una medición de pulso en curso.

La intensidad no se puede cambiar: el motor solo prende o apaga, y la luz verde del aviso va junto con la vibración.

## Requisitos

- Windows 10 o posterior
- Python 3
- Bluetooth encendido
- Las bandas desconectadas de la app del teléfono

## Uso

```bat
pip install -r requirements.txt
abrir-server.bat
```

También se puede arrancar con `python server.py`. La ventana que se abre es el servidor: tiene que quedar abierta. Cerrarla lo detiene. La página queda en [http://127.0.0.1:8000](http://127.0.0.1:8000).

La primera vez, en el lado vacío tocá **Buscar**. La banda tiene que estar desconectada del teléfono. Cuando aparece, **Agregar** la asigna a ese lado. La elección queda en `bandas.json`, que es local y no se versiona.

Si el `.bat` se abre con el servidor ya corriendo, esa ventana toma el puerto y pasa a ser la dueña.

## Protocolo

Los comandos son paquetes de 16 bytes. El último byte es la suma de los anteriores. Se escriben en la característica `6e400002-b5a3-f393-e0a9-e50e24dcca9e`.

| Acción | Inicio del paquete |
| --- | --- |
| Find Device | `50 55 AA` |
| Inicio de llamada | `72 00 01 01` |
| Corte | `72 04 01 01` |
| Toque corto | `72 01 01 01` |
| Batería | `03` |
| Apagar pulso automático | `16 02 02 00` |

## Línea de comandos

`vibrar.py` manda un solo comando y se desconecta. Hay que tener la banda libre, también del servidor.

```bat
python vibrar.py find
python vibrar.py short
python vibrar.py long
python vibrar.py stop
python vibrar.py find --sensors-off
```
