// Onnix SA Panel — Conversations auto-scroll + selected ID tracking

/* ========================================
   AUTOSCROLL — C5 unified implementation
   ======================================== */

/**
 * Umbral (px) desde el fondo por debajo del cual se considera "cerca del fondo".
 * Si el usuario scrolleó hacia arriba más de este valor, no forzamos scroll
 * salvo que sea un mensaje propio recién enviado (force=true).
 */
var SCROLL_THRESHOLD = 150;

/**
 * Rastrea si el usuario scrolleó manualmente hacia arriba en el hilo.
 * Se resetea a false cuando se carga una nueva conversación (swap de #conv-thread).
 */
var _userScrolledUp = false;

/**
 * El elemento que scrollea el hilo.
 *
 * Era #conv-thread. Dejó de serlo cuando el panel pasó a columna flex para que
 * el composer quede fijo abajo: si #conv-thread siguiera scrolleando, el campo
 * de escribir se iría con los mensajes, que es el bug que esto arregla.
 * #conv-thread sigue siendo el TARGET de los swaps de htmx — son dos cosas
 * distintas, y por eso esto es una función y no un getElementById suelto.
 */
function threadScroller() {
    return document.getElementById('message-list');
}

/**
 * Engancha el detector de scroll manual al scroller actual.
 * Se llama de nuevo en cada swap de #conv-thread porque el nodo es otro.
 */
function watchThreadScroll() {
    var el = threadScroller();
    if (!el || el._scrollWatched) return;
    el._scrollWatched = true;
    el.addEventListener('scroll', function() {
        _userScrolledUp = !isNearBottom(el, SCROLL_THRESHOLD);
    }, { passive: true });
}

/**
 * Check if a scrollable element is near the bottom.
 */
function isNearBottom(el, threshold) {
    if (!el) return false;
    threshold = (threshold !== undefined) ? threshold : SCROLL_THRESHOLD;
    return el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
}

/**
 * Scroll a container to the bottom (instantáneo).
 */
function scrollToBottom(el) {
    if (!el) return;
    el.scrollTop = el.scrollHeight;
}

/**
 * Función unificada de autoscroll del thread.
 *
 * @param {boolean} force - Si true, scrollea siempre (mensaje propio enviado).
 *                          Si false, respeta el estado de scroll manual del usuario.
 */
function scrollThreadToBottom(force) {
    var container = threadScroller();
    if (!container) return;
    if (force || !_userScrolledUp) {
        setTimeout(function() { scrollToBottom(container); }, 50);
    }
}

/**
 * Detectar scroll manual del usuario en el hilo.
 * Si sube más del umbral, marcamos _userScrolledUp = true.
 * Si vuelve al fondo, lo limpiamos.
 */
document.addEventListener('DOMContentLoaded', function() {
    watchThreadScroll();
    // Scroll inicial al cargar la página con una conversación abierta
    scrollToBottom(threadScroller());
});

/**
 * After any HTMX swap, auto-scroll the conversation thread if appropriate.
 * - #conv-thread swap: nueva conversación cargada → siempre scrollear + reset flag
 * - #message-list swap: mensajes polled → respetar scroll manual
 * - #reply-form swap (OOB reset tras envío): mensaje propio → forzar scroll
 */
document.addEventListener('htmx:afterSwap', function(event) {
    var target = event.detail.target;
    if (target && target.id === 'conv-thread') {
        // Nueva conversación cargada: el scroller es OTRO nodo, así que hay que
        // volver a engancharlo. Antes se le sumaba un listener más al mismo
        // elemento en cada swap.
        _userScrolledUp = false;
        watchThreadScroll();
        scrollThreadToBottom(true);
        return;
    }

    if (target && target.id === 'message-list') {
        // Mensajes polled vía SSE: respetar scroll manual
        scrollThreadToBottom(false);
        return;
    }
});

/**
 * After OOB swaps (reply sends): el mensaje del agente se appendeó a #message-list.
 * Forzar scroll porque es un mensaje propio recién enviado.
 */
document.addEventListener('htmx:oobAfterSwap', function(event) {
    var target = event.detail.target;
    if (target && target.id === 'message-list') {
        scrollThreadToBottom(true);
    }
});

/* ========================================
   GLOBAL ERROR TOAST
   ======================================== */

/**
 * Emitir un toast. UN solo renderizador: el de base.html, que escucha
 * `showToast` con Alpine. Antes esta funcion armaba su propio <div> en
 * #toast-container, arriba a la derecha, con su propia region aria-live —
 * la segunda de la aplicacion, y por eso el lector anunciaba todo dos veces.
 *
 * El nombre del evento es camelCase a proposito: es el mismo que manda el
 * servidor en HX-Trigger, asi que cliente y servidor entran por la misma
 * puerta.
 *
 * @param {string} message - Texto a mostrar.
 * @param {string} type - 'error' o cualquier otra cosa, que cae en neutro.
 */
function showToast(message, type) {
    window.dispatchEvent(new CustomEvent('showToast', {
        detail: { type: type || 'error', message: message },
    }));
}

/**
 * Check if an HTMX request was triggered by a polling element (hx-trigger="every ...").
 * We skip error toasts for these to avoid spamming the user during transient network issues.
 */
function isPollingRequest(event) {
    var elt = event.detail.elt;
    if (!elt) return false;
    var trigger = elt.getAttribute('hx-trigger') || '';
    return trigger.indexOf('every') !== -1;
}

/**
 * HTMX beforeSwap: allow 4xx responses to swap into the target so that
 * inline validation errors (e.g. "Contraseña actual incorrecta") render
 * naturally in the form's feedback div. Without this HTMX discards 4xx
 * bodies and the user only sees the generic toast — confusing UX.
 *
 * `isError` NO se toca. htmx deriva el resultado de ahí:
 *   i.failed = isError;  i.successful = !isError;
 * así que ponerlo en false hacía que un 422 llegara a htmx:afterRequest
 * marcado como éxito, y los templates que preguntan por
 * `$event.detail.successful` cerraban el modal, reseteaban el form y
 * mostraban un toast verde sin que el servidor hubiera guardado nada
 * (settings.html, auth_audit_table.html).
 *
 * El swap del cuerpo 4xx no depende de isError: htmx evalúa `shouldSwap`
 * aparte. El toast redundante se silencia en el handler de abajo.
 */
document.addEventListener('htmx:beforeSwap', function(event) {
    var status = event.detail.xhr ? event.detail.xhr.status : 0;
    if (status >= 400 && status < 500) {
        event.detail.shouldSwap = true;
    }
});

/**
 * HTMX responseError: dispara siempre que isError quedó en true, o sea en
 * todo 4xx y 5xx. Se salta:
 *   - polling, para no spamear ante fallos de red transitorios;
 *   - los estados que ya explicaron el error DENTRO del formulario (400,
 *     422), donde el toast sería una segunda copia del mismo mensaje.
 *
 * 403 (CSRF) y 404 sí toastean: su cuerpo no le dice al usuario qué corregir.
 */
document.addEventListener('htmx:responseError', function(event) {
    if (isPollingRequest(event)) return;
    var xhr = event.detail.xhr;
    var status = xhr ? xhr.status : 0;
    if (window.HtmxErrorPolicy && window.HtmxErrorPolicy.rendersInlineFormError(status)) {
        return;
    }
    var msg = 'Error del servidor';
    if (status === 403) {
        msg = 'No tienes permiso para esta accion';
    } else if (status === 404) {
        msg = 'Recurso no encontrado';
    } else if (status >= 500) {
        msg = 'Error interno del servidor';
    }
    showToast(msg, 'error');
});

/**
 * HTMX sendError: network failure, timeout, etc.
 * Skip polling requests.
 */
document.addEventListener('htmx:sendError', function(event) {
    if (isPollingRequest(event)) return;
    showToast('Error de conexion. Verifica tu internet.', 'warning');
});
