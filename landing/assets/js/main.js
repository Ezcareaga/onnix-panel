/* ═══════════════════════════════════════
   Onnix SA — Landing Page Scripts
   ═══════════════════════════════════════ */

// ─── Nav scroll effect ───
(function () {
    var nav = document.getElementById('nav');
    window.addEventListener('scroll', function () {
        nav.classList.toggle('scrolled', window.scrollY > 60);
    }, { passive: true });
})();

// ─── Scroll reveal ───
(function () {
    var elementos = document.querySelectorAll('.destacada, .carril, .barrio, .contact-option');
    if (!elementos.length) { return; }

    // Antes esto ponia opacity:0 y se lo sacaba al entrar en viewport. Si el JS
    // no corria — bloqueado, error antes de esta linea, red caida a mitad de
    // carga — tres de las cinco secciones de la pagina quedaban invisibles para
    // siempre. Ahora el HTML nace visible y el efecto solo se monta cuando ya
    // sabemos que hay JS y que el visitante no pidio menos movimiento.
    if (!('IntersectionObserver' in window)) { return; }
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) { return; }

    var observer = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
            if (entry.isIntersecting) {
                entry.target.style.opacity = '1';
                entry.target.style.transform = 'translateY(0)';
                observer.unobserve(entry.target);
            }
        });
    }, { threshold: 0.15 });

    elementos.forEach(function (el) {
        el.style.opacity = '0';
        el.style.transform = 'translateY(30px)';
        el.style.transition = 'opacity 0.2s ease-out, transform 0.2s ease-out';
        observer.observe(el);
    });
})();
