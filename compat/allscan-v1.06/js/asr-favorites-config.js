(() => {
  'use strict'

  const apiUrl = new URL('../asr-api.php', window.location.href)

  async function apiRequest(action, data = null) {
    const options = {
      credentials: 'same-origin',
      cache: 'no-store',
    }
    apiUrl.search = new URLSearchParams({ action }).toString()
    if (data) {
      options.method = 'POST'
      options.headers = { 'Content-Type': 'application/x-www-form-urlencoded' }
      options.body = new URLSearchParams({ action, ...data }).toString()
    }
    const response = await fetch(apiUrl.toString(), options)
    const payload = await response.json()
    if (!response.ok || payload.ok === false) throw new Error(payload.error || 'Favorites operation failed.')
    return payload
  }

  function manageFavoritesSection() {
    return [...document.querySelectorAll('.greenborder')].find((section) => (
      section.querySelector(':scope > h1')?.textContent.trim() === 'Manage Favorites'
    ))
  }
  function buildImporter(section) {
    const panel = document.createElement('section')
    panel.className = 'asr-supermon-import'
    const heading = document.createElement('h2')
    heading.textContent = 'Import Supermon Favorites'
    const help = document.createElement('p')
    help.textContent = 'Preview and merge compatible Supermon favorites.ini entries. Existing Favorites, order, descriptions, and colors are preserved.'
    const controls = document.createElement('div')
    controls.className = 'asr-supermon-import-controls'
    const label = document.createElement('label')
    label.textContent = 'Import into'
    const select = document.createElement('select')
    select.setAttribute('aria-label', 'Favorites file to receive imported entries')
    label.append(select)
    const button = document.createElement('button')
    button.type = 'button'
    button.textContent = 'Preview Import'
    button.disabled = true
    const status = document.createElement('p')
    status.className = 'asr-supermon-import-status'
    status.setAttribute('role', 'status')
    controls.append(label, button)
    panel.append(heading, help, controls, status)
    section.querySelector(':scope > h1')?.insertAdjacentElement('afterend', panel)

    apiRequest('favorites').then((payload) => {
      const files = Array.isArray(payload.files) ? payload.files.filter((file) => (
        typeof file.value === 'string' && file.modifiable === true
      )) : []
      for (const file of files) {
        const option = document.createElement('option')
        option.value = file.value
        option.textContent = file.label || file.value.split('/').pop()
        if (file.value === payload.selectedFile) option.selected = true
        select.append(option)
      }
      if (!files.length) {
        status.textContent = 'No shared Favorites file is available for a safe import.'
        return
      }
      button.disabled = false
    }).catch((error) => {
      status.textContent = error.message
    })

    button.addEventListener('click', async () => {
      if (!select.value) return
      button.disabled = true
      status.textContent = 'Checking the Supermon Favorites file…'
      try {
        const preview = await apiRequest('favorite-manage', {
          operation: 'preview-import',
          favsfile: select.value,
          node: '',
          value: '',
        })
        if (preview.available === false) {
          status.textContent = 'No compatible Supermon favorites.ini file was found.'
          return
        }
        if (preview.malformed) {
          status.textContent = 'The Supermon Favorites file contains no compatible entries; nothing was changed.'
          return
        }
        const additions = Array.isArray(preview.additions) ? preview.additions.length : 0
        const existing = Array.isArray(preview.existing) ? preview.existing.length : 0
        const confirmed = window.confirm('Import preview: add ' + additions + ' new Favorite(s); preserve ' + existing + ' existing match(es). Existing order, descriptions, and colors will not be replaced. Continue?')
        if (!confirmed) {
          status.textContent = 'Import canceled; nothing was changed.'
          return
        }
        const result = await apiRequest('favorite-manage', {
          operation: 'import',
          favsfile: select.value,
          node: '',
          value: '',
        })
        status.textContent = 'Imported ' + Number(result.added || 0) + ' new Favorite(s). Existing entries and customizations were preserved.'
      } catch (error) {
        status.textContent = error.message
      } finally {
        button.disabled = false
      }
    })
  }

  document.addEventListener('DOMContentLoaded', () => {
    if (!document.body.classList.contains('asr-admin-page-cfg')) return
    const section = manageFavoritesSection()
    if (section) buildImporter(section)
  })
})()
