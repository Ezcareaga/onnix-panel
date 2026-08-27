#!/usr/bin/env node
// Hook con alcance al subagente `architect`.
// La restricción es la clave del diseño: el que planifica NO edita panel/.
// Sin esto, "no toques el código" es una sugerencia; con esto es un exit code.
//
// Adaptado desde e-commerce/.claude/hooks/guard-architect.js el 2026-08-17.
// Dos cambios respecto del original: la carpeta de planes es `.planning/`
// (Onnix ya tiene esa estructura, con ROADMAP/STATE/TECHNICAL_DEBT adentro), y
// el gestor de paquetes es pip, no pnpm.
'use strict'

const path = require('node:path')

let raw = ''
process.stdin.on('data', (c) => (raw += c))
process.stdin.on('end', () => {
  let input
  try {
    input = JSON.parse(raw)
  } catch {
    process.exit(0)
  }

  const tool = input.tool_name ?? ''
  const ti = input.tool_input ?? {}

  const negar = (motivo) => {
    process.stdout.write(
      JSON.stringify({
        hookSpecificOutput: {
          hookEventName: 'PreToolUse',
          permissionDecision: 'deny',
          permissionDecisionReason: `El arquitecto no escribe código. ${motivo}`,
        },
      }),
    )
    process.exit(0)
  }

  // Escritura de archivos: solo dentro de .planning/.
  if (/^(Write|Edit|MultiEdit|NotebookEdit)$/.test(tool)) {
    const file = ti.file_path ?? ti.filePath ?? ''
    const rel = path.relative(process.cwd(), file).replace(/\\/g, '/')
    if (!rel.startsWith('.planning/')) {
      negar(
        `Intentó escribir en ${rel || '(sin ruta)'}. Solo puede escribir en .planning/. ` +
          'Describí el cambio en el plan y dejá que el editor lo implemente.',
      )
    }
    process.exit(0)
  }

  // Bash: bloquear escritura por la puerta de atrás.
  if (tool === 'Bash') {
    const cmd = String(ti.command ?? '')
    const escribe = [
      />\s*(?!\/dev\/null)/, // redirección a archivo
      /\btee\b/,
      /\b(cp|mv|rm|mkdir|touch|install)\b/,
      /\bsed\b[^|]*-i/,
      /\bgit\s+(commit|merge|rebase|push|checkout\s+-b|apply|restore)\b/,
      /\bpip\s+(install|uninstall)\b/,
      /\balembic\s+(upgrade|downgrade|revision|stamp)\b/,
      /\bdocker\s+compose\s+(up|down|build|restart)\b/,
    ]
    if (escribe.some((r) => r.test(cmd))) {
      negar(`El comando modifica el árbol o el entorno: \`${cmd.slice(0, 120)}\`. Usá solo lectura e inspección.`)
    }
  }

  process.exit(0)
})
