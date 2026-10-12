const form = document.querySelector('#sum-form');
const button = document.querySelector('#calculate');
const result = document.querySelector('#result');
const resultValue = document.querySelector('#result-value');
const savedAt = document.querySelector('#saved-at');
const message = document.querySelector('#message');
const status = document.querySelector('#status');

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  message.hidden = true;
  result.hidden = true;

  if (!form.reportValidity()) return;
  const a = Number(document.querySelector('#number-a').value);
  const b = Number(document.querySelector('#number-b').value);
  if (!Number.isFinite(a) || !Number.isFinite(b) || Math.abs(a) > 1e12 || Math.abs(b) > 1e12) {
    message.textContent = 'Ingresa dos números válidos entre -10¹² y 10¹².';
    message.hidden = false;
    return;
  }

  button.disabled = true;
  status.textContent = 'Guardando…';
  try {
    const response = await fetch('/api/sumar', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ a, b }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.guardado) {
      throw new Error(payload.error || 'No se pudo guardar la operación.');
    }
    resultValue.textContent = new Intl.NumberFormat('es-CL', { maximumFractionDigits: 12 }).format(payload.resultado);
    savedAt.textContent = `Guardado en base de datos · ${new Date(payload.fecha_hora_utc).toLocaleString('es-CL')}`;
    result.hidden = false;
    status.textContent = 'Guardado correctamente';
  } catch (error) {
    message.textContent = error.message || 'Ocurrió un error de conexión.';
    message.hidden = false;
    status.textContent = 'Error al guardar';
  } finally {
    button.disabled = false;
  }
});
