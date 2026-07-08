(function () {
  function isMobileCfgViewport() {
    return window.matchMedia('(max-width: 700px) and (orientation: portrait)').matches
  }

  function findCfgNameSelects() {
    var directMatches = Array.prototype.slice.call(document.querySelectorAll('.asr-cfg-name-select'))
    if (directMatches.length) return directMatches

    if (!document.body.classList.contains('asr-admin-page-cfg')) return []

    return Array.prototype.slice.call(document.querySelectorAll('fieldset')).reduce(function (matches, fieldset) {
      var legend = fieldset.querySelector('legend')
      if (!legend || legend.textContent.trim() !== 'Edit Cfg') return matches

      Array.prototype.slice.call(fieldset.querySelectorAll('label')).forEach(function (label) {
        if (label.textContent.indexOf('Cfg Name') === -1) return
        var select = label.querySelector('select')
        if (select) {
          fieldset.closest('form')?.classList.add('asr-cfg-edit-form')
          select.classList.add('asr-cfg-name-select')
          matches.push(select)
        }
      })

      return matches
    }, [])
  }

  function collapseSelect(select) {
    select.removeAttribute('size')
    select.classList.remove('asr-select-expanded')
  }

  function expandSelect(select) {
    if (!isMobileCfgViewport()) return false
    select.setAttribute('size', String(Math.min(select.options.length, 8)))
    select.classList.add('asr-select-expanded')
    select.focus({ preventScroll: true })
    select.scrollIntoView({ block: 'nearest', inline: 'nearest' })
    return true
  }

  function initCfgNameSelect(select) {
    select.addEventListener('pointerdown', function (event) {
      if (!isMobileCfgViewport() || select.classList.contains('asr-select-expanded')) return
      event.preventDefault()
      expandSelect(select)
    })

    select.addEventListener('focus', function () {
      if (isMobileCfgViewport()) expandSelect(select)
    })

    select.addEventListener('change', function () {
      collapseSelect(select)
    })

    select.addEventListener('blur', function () {
      window.setTimeout(function () {
        collapseSelect(select)
      }, 120)
    })

    select.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' || event.key === 'Enter') {
        collapseSelect(select)
      }
    })

    window.addEventListener('resize', function () {
      if (!isMobileCfgViewport()) collapseSelect(select)
    })
  }

  function init() {
    findCfgNameSelects().forEach(initCfgNameSelect)
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init)
  } else {
    init()
  }
})()
