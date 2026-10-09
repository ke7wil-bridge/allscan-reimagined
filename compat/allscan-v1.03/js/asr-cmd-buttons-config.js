(() => {
  'use strict'

  const COMMAND_PATTERN = /^\*[0-9A-Da-d#;]{1,40}$/

  function button(label, className, action) {
    const control = document.createElement('button')
    control.type = 'button'
    control.textContent = label
    control.className = className
    control.addEventListener('click', action)
    return control
  }

  function parseEntry(value) {
    const text = value.trim()
    const match = text.match(/^(.*?)(\*[0-9A-Da-d#;]{1,40})$/)
    if (!match) return { label: text, command: '' }
    return {
      label: match[1].trim(),
      command: match[2],
    }
  }

  function enhanceCommandEditor(form, valueInput) {
    valueInput.type = 'hidden'
    const editor = document.createElement('div')
    editor.className = 'asr-cmd-config-editor'
    const help = document.createElement('p')
    help.className = 'asr-cmd-config-help'
    help.textContent = 'Create labeled DTMF command buttons. Commands must begin with *; commas are reserved as separators.'
    editor.append(help)
    const rows = document.createElement('div')
    rows.className = 'asr-cmd-config-rows'
    editor.append(rows)

    function addRow(entry = { label: '', command: '' }) {
      const row = document.createElement('div')
      row.className = 'asr-cmd-config-row'
      const label = document.createElement('input')
      label.type = 'text'
      label.maxLength = 40
      label.placeholder = 'Button name'
      label.value = entry.label
      label.setAttribute('aria-label', 'Command button name')
      const command = document.createElement('input')
      command.type = 'text'
      command.maxLength = 41
      command.placeholder = '*712'
      command.value = entry.command
      command.setAttribute('aria-label', 'DTMF command')
      const actions = document.createElement('span')
      actions.className = 'asr-cmd-config-row-actions'
      actions.append(
        button('↑', 'asr-cmd-config-move', () => {
          if (row.previousElementSibling) rows.insertBefore(row, row.previousElementSibling)
        }),
        button('↓', 'asr-cmd-config-move', () => {
          if (row.nextElementSibling) rows.insertBefore(row.nextElementSibling, row)
        }),
        button('Delete', 'asr-cmd-config-delete', () => {
          const name = label.value.trim() || command.value.trim() || 'this command'
          if (window.confirm(`Delete ${name}?`)) row.remove()
        }),
      )
      row.append(label, command, actions)
      rows.append(row)
    }

    const initial = valueInput.value
      .split(',')
      .map(parseEntry)
      .filter((entry) => entry.label || entry.command)
    initial.forEach(addRow)
    editor.append(button('+ Add command', 'asr-cmd-config-add', () => addRow()))

    form.addEventListener('submit', (event) => {
      if (event.submitter?.value === 'Cancel') return
      const values = []
      for (const row of rows.querySelectorAll('.asr-cmd-config-row')) {
        const inputs = row.querySelectorAll('input')
        const label = inputs[0].value.trim()
        const command = inputs[1].value.trim()
        if (!label && !command) continue
        if (!COMMAND_PATTERN.test(command)) {
          event.preventDefault()
          window.alert(`Enter a valid DTMF command beginning with * for "${label || 'unnamed command'}".`)
          inputs[1].focus()
          return
        }
        if (label.includes(',')) {
          event.preventDefault()
          window.alert('Command names cannot contain commas.')
          inputs[0].focus()
          return
        }
        values.push(`${label ? `${label} ` : ''}${command}`)
      }
      valueInput.value = values.join(', ')
    })

    valueInput.insertAdjacentElement('afterend', editor)
  }

  function enhanceVisibilityToggle(select) {
    select.hidden = true
    const wrapper = document.createElement('label')
    wrapper.className = 'asr-cmd-config-toggle'
    const checkbox = document.createElement('input')
    checkbox.type = 'checkbox'
    checkbox.checked = select.value === '1'
    checkbox.addEventListener('change', () => {
      select.value = checkbox.checked ? '1' : '0'
    })
    wrapper.append(checkbox, document.createTextNode(' Show the Cmds dropdown in Node Controls'))
    select.insertAdjacentElement('afterend', wrapper)
  }

  document.addEventListener('DOMContentLoaded', () => {
    if (!document.body.classList.contains('asr-admin-page-cfg')) return
    const form = document.getElementById('editCfgForm')
    const cfgId = form?.querySelector('input[name="cfg_id"]')?.value
    if (cfgId === '12') {
      const input = form.querySelector('input[name="val"]')
      if (input) enhanceCommandEditor(form, input)
    }
    if (cfgId === '14') {
      const select = form.querySelector('select[name="val"]')
      if (select) enhanceVisibilityToggle(select)
    }
  })
})()
