(function (root) {
  'use strict'
  var definitions = [['basics','Basics'],['controls','Controls & Permissions'],['clients','Client Data'],['diagnostics','Status'],['advanced','Advanced']]
  function sectionTab(section) {
    if (section.matches('.asr-card-basics-section,.asr-backend-choice-section')) return 'basics'
    if (section.matches('.asr-destination-permission-section,.asr-standard-bridge-settings')) return 'controls'
    if (section.matches('.asr-standard-dmr-tgif,.asr-connected-client-settings')) return 'clients'
    if (section.matches('.asr-backend-readiness-section')) return 'diagnostics'
    return 'advanced'
  }
  function activate(scope, selected, focus) {
    var selectedButton = null
    scope.querySelectorAll('.asr-bridge-editor-tab').forEach(function (button) {
      var active = button.dataset.bridgeTab === selected
      button.classList.toggle('is-active', active)
      button.setAttribute('aria-selected', active ? 'true' : 'false')
      button.tabIndex = active ? 0 : -1
      if (active) selectedButton = button
    })
    scope.querySelectorAll('.asr-bridge-panel-section').forEach(function (section) {
      section.dataset.modernTabHidden = sectionTab(section) === selected ? 'false' : 'true'
    })
    if (focus && selectedButton) selectedButton.focus()
  }
  function install(scope, documentRef) {
    var prior = scope.querySelector('.asr-bridge-editor-tabs')
    if (prior) prior.remove()
    scope.querySelectorAll('.asr-bridge-panel-section').forEach(function (section) { delete section.dataset.modernTabHidden })
    var available = {}, labels = {}
    scope.querySelectorAll('.asr-bridge-panel-section').forEach(function (section) {
      if (!section.hidden) {
        var tab = sectionTab(section)
        available[tab] = true
        if (!labels[tab] && section.dataset.bridgeTabLabel) labels[tab] = section.dataset.bridgeTabLabel
      }
    })
    var visible = definitions.filter(function (definition) { return available[definition[0]] })
    if (!visible.length) return
    var tablist = documentRef.createElement('div')
    tablist.className = 'asr-bridge-editor-tabs'
    tablist.setAttribute('role','tablist')
    visible.forEach(function (definition) {
      var button = documentRef.createElement('button')
      button.type = 'button'; button.className = 'asr-bridge-editor-tab'; button.dataset.bridgeTab = definition[0]
      button.textContent = labels[definition[0]] || definition[1]; button.setAttribute('role','tab')
      button.addEventListener('click',function(){ activate(scope, definition[0], false) })
      tablist.appendChild(button)
    })
    tablist.addEventListener('keydown', function (event) {
      if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return
      var buttons = Array.prototype.slice.call(tablist.querySelectorAll('.asr-bridge-editor-tab'))
      var current = buttons.indexOf(event.target); if (current < 0) return
      event.preventDefault()
      var next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (current + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length
      activate(scope, buttons[next].dataset.bridgeTab, true)
    })
    scope.insertBefore(tablist, scope.firstChild)
    activate(scope, visible[0][0], false)
  }
  root.AsrSettingsBridgeTabs = { sectionTab:sectionTab, activate:activate, install:install }
})(typeof window !== 'undefined' ? window : globalThis);

(function () {
  'use strict'
  if (!document.body.classList.contains('asr-admin-page-settings-modern')) return
  var shell = document.querySelector('.asr-settings-modern-shell')
  var nav = document.querySelector('.asr-settings-nav')
  var form = document.querySelector('.asr-reimagined-settings-form')
  var base = window.location.pathname.split('/asr-settings/')[0] || ''
  var performanceTimer = 0
  var bridgeEditor = document.getElementById('asrBridgeEditorDialog')
  var bridgeHost = bridgeEditor && bridgeEditor.querySelector('[data-bridge-editor-host]')
  var activeBridge = null
  var bridgeSnapshot = ''
  var bridgeOrigin = null
  if (!shell || !nav) return

  function escapeText(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (char) {
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[char]
    })
  }
  function request(action, options) {
    options = options || {}; options.credentials = 'same-origin'; options.cache = 'no-store'
    options.headers = Object.assign({'X-ASR-Requested-With':'settings-admin'}, options.headers || {})
    return fetch(base + '/asr-api.php?action=' + encodeURIComponent(action), options).then(function (response) {
      return response.text().then(function (text) { var data; try { data=JSON.parse(text) } catch(error) { throw new Error('The node returned an invalid structured response.') } return data }).then(function (data) {
        if (!response.ok || data.ok === false) throw new Error(data.error || 'The request failed.')
        return data
      })
    })
  }
  function compactNavigation() {
    var label = document.createElement('label'); var select = document.createElement('select')
    label.className = 'asr-settings-compact-nav'; label.innerHTML = '<span>Settings category</span>'
    nav.querySelectorAll('a').forEach(function (link) {
      var option = document.createElement('option'); option.value = link.href
      option.textContent = (link.querySelector('strong') || link).textContent.trim()
      option.selected = link.getAttribute('aria-current') === 'page'; select.appendChild(option)
    })
    select.addEventListener('change', function () { window.location.assign(select.value) })
    label.appendChild(select); nav.parentNode.insertBefore(label, nav)
  }
  function flattenSections() {
    document.querySelectorAll('.asr-settings-section:not([hidden])').forEach(function (section) {
      section.classList.remove('is-collapsed')
      var button = section.querySelector(':scope > legend .asr-settings-section-toggle')
      if (!button) return
      var title = document.createElement('span'); title.className = 'asr-settings-section-title'
      title.textContent = button.childNodes[0] ? button.childNodes[0].textContent.trim() : button.textContent.trim()
      button.replaceWith(title)
    })
  }

  var bridgeTabs = window.AsrSettingsBridgeTabs
  function refreshBridgeTabs(row) { var body=row._asrEditorBody||row.querySelector('.asr-bridge-panel-body');if(body)bridgeTabs.install(body,document) }
  function enhanceBridgeRow(row) {
    var body = row.querySelector('.asr-bridge-panel-body'); if (!body || body.dataset.enhanced) return
    body.dataset.enhanced = '1'; row._asrEditorBody=body; row.classList.add('is-collapsed'); var rowIcon=row.querySelector('.asr-bridge-toggle .asr-settings-toggle-icon');if(rowIcon)rowIcon.textContent='>'; refreshBridgeTabs(row)
    body.addEventListener('change',function(event){if(event.target&&/^(bridgeMode\[\]|bridgeCardType\[\]|bridgeBackendMode\[\])$/.test(event.target.name||''))setTimeout(function(){refreshBridgeTabs(row)},0)})
    var toggle=row.querySelector('.asr-bridge-toggle'); if(toggle) toggle.addEventListener('click',function(event){event.preventDefault();event.stopPropagation();openBridgeEditor(row)})
  }
  function inputState(container) { return JSON.stringify(Array.prototype.map.call(container.querySelectorAll('input,select,textarea'),function(field){return {value:field.value,checked:field.checked,selected:field.selectedIndex}})) }
  function restoreState(container,state){JSON.parse(state||'[]').forEach(function(value,index){var field=container.querySelectorAll('input,select,textarea')[index];if(!field)return;field.value=value.value;field.checked=value.checked;if(field.tagName==='SELECT')field.selectedIndex=value.selected;field.dispatchEvent(new Event('change',{bubbles:true}))})}
  function openBridgeEditor(row) {
    if(!bridgeEditor||!bridgeHost||activeBridge)return;var body=row.querySelector('.asr-bridge-panel-body');if(!body)return
    activeBridge=row;bridgeSnapshot=inputState(row);bridgeOrigin=document.createComment('bridge-origin');body.parentNode.insertBefore(bridgeOrigin,body);bridgeHost.appendChild(body);body.querySelectorAll('[name]').forEach(function(field){field.setAttribute('form','asrReimaginedSettingsForm')})
    var name=row.querySelector('.asr-bridge-panel-name');document.getElementById('asrBridgeEditorTitle').textContent=name?name.textContent:'Configure Bridge'
    var footer=bridgeEditor.querySelector('footer');var old=footer.querySelector('[data-modal-delete]');if(old)old.remove();var source=row.querySelector('.asr-bridge-delete')
    if(source){var del=document.createElement('button');del.type='button';del.dataset.modalDelete='1';del.className='asr-destructive-action';del.textContent=row.dataset.ownershipState==='owned'?'Remove Bridge and Managed Resources':'Remove Bridge from ASR';del.disabled=source.disabled;del.addEventListener('click',function(){source.click();if(!row.isConnected)form.requestSubmit()});footer.insertBefore(del,footer.firstChild)}
    var icon=row.querySelector('.asr-bridge-toggle .asr-settings-toggle-icon');if(icon)icon.textContent='>';bridgeEditor.hidden=false;document.body.classList.add('asr-modal-open');var first=body.querySelector('select,input,button');if(first)first.focus()
  }
  function closeBridgeEditor(cancel){if(!activeBridge)return;var row=activeBridge;var body=bridgeHost.querySelector('.asr-bridge-panel-body');if(cancel)restoreState(row,bridgeSnapshot);if(body&&bridgeOrigin.parentNode)bridgeOrigin.parentNode.insertBefore(body,bridgeOrigin);if(bridgeOrigin.parentNode)bridgeOrigin.remove();row.classList.add('is-collapsed');var icon=row.querySelector('.asr-bridge-toggle .asr-settings-toggle-icon');if(icon)icon.textContent='>';bridgeEditor.hidden=true;activeBridge=null;bridgeOrigin=null;document.body.classList.remove('asr-modal-open')}
  function enhanceUrf(){var panel=document.querySelector('[data-urf-panel]:not([hidden])');var table=document.querySelector('.asr-bridge-settings-table');if(!panel||!table||panel.closest('.asr-bridge-settings-row'))return;var row=document.createElement('div');row.className='asr-bridge-settings-row asr-urf-settings-row is-collapsed';var node=(panel.querySelector('[name="urfNode"]')||{}).value||'not set';row.innerHTML='<div class="asr-bridge-panel-header"><button type="button" class="asr-bridge-toggle"><span class="asr-settings-toggle-icon">&gt;</span><span class="asr-bridge-toggle-copy"><strong class="asr-bridge-panel-name">URFWIL Reflector</strong><span class="asr-bridge-panel-summary">Node '+escapeText(node)+' · Select to configure this reflector.</span></span><span class="asr-bridge-capability-note">Shared reflector</span></button></div><div class="asr-bridge-panel-body"></div>';row.querySelector('.asr-bridge-panel-body').appendChild(panel);table.insertBefore(row,table.firstChild);enhanceBridgeRow(row)}

  function openModal(modal){if(!modal)return;modal.hidden=false;document.body.classList.add('asr-modal-open');var close=modal.querySelector('.asr-modal-close');if(close)close.focus()}
  function closeModal(modal){if(!modal)return;modal.hidden=true;document.body.classList.remove('asr-modal-open');if(modal.id==='asrAdminDialog'){clearInterval(performanceTimer);performanceTimer=0}}
  function renderDiagnostics(data){var target=document.getElementById('asr-bridge-diagnostics');if(!target)return;var rows=Array.isArray(data.bridges)?data.bridges:[];target.innerHTML=rows.map(function(bridge){var state=bridge.linked||'unknown';var label=state==='connected'?'Connected':state==='disconnected'?'Disconnected':'Cannot confirm';var ready=bridge.readiness||{};var warnings=Array.isArray(bridge.warnings)?bridge.warnings:[];var missing=Array.isArray(ready.missing)?ready.missing:[];return '<article class="asr-diagnostics-summary is-'+escapeText(state)+'"><header><div><strong>'+escapeText(bridge.title)+'</strong><span>AllStar node '+escapeText(bridge.node||'not set')+'</span></div><span class="asr-status-badge">'+label+'</span></header><div class="asr-diagnostics-at-glance"><span><small>What ASR can verify</small><b>'+escapeText(ready.summary||'ASR could not determine the bridge state. Check its service and AllStar link.')+'</b></span><span><small>Client or talker records</small><b>'+Number(bridge.clientCount||0)+'</b></span></div>'+(warnings.length?'<p class="asr-diagnostics-warning">'+escapeText(warnings[0])+'</p>':'')+'<details class="asr-progressive-details"><summary><span class="asr-disclosure-chevron">&gt;</span> Technical details</summary><dl><dt>Link-state source</dt><dd>'+escapeText(data.linkStateSource||'unavailable')+'</dd><dt>Client/talker source</dt><dd>'+escapeText(bridge.clientSource||'automatic')+'</dd><dt>Management mode</dt><dd>'+escapeText(bridge.backendMode||'managed')+'</dd></dl>'+(missing.length?'<ul>'+missing.map(function(item){return '<li>'+escapeText(item)+'</li>'}).join('')+'</ul>':'')+'</details></article>'}).join('')||'<p>No configured bridges were found.</p>'}
  function loadDiagnostics(){var target=document.getElementById('asr-bridge-diagnostics');if(!target)return;fetch(base+'/asr-api.php?action=bridge-diagnostics',{credentials:'same-origin',cache:'no-store'}).then(function(response){return response.json().then(function(data){if(!response.ok)throw new Error(data.error||'Diagnostics unavailable.');return data})}).then(renderDiagnostics).catch(function(error){target.innerHTML='<p class="asr-bridge-diagnostics-error">'+escapeText(error.message)+'</p>'})}

  function field(label,name,value,type){return '<label><span>'+label+'</span><input name="'+name+'" type="'+(type||'text')+'" value="'+escapeText(value||'')+'"></label>'}
  function userEditor(data,user){user=user||{id:0,name:'',email:'',location:'',nodes:[],permission:2,timezoneId:0};var permissions=data.permissions.map(function(o){return '<option value="'+o.value+'"'+(o.value===user.permission?' selected':'')+'>'+escapeText(o.label)+'</option>'}).join('');var zones=data.timezones.map(function(o){return '<option value="'+o.value+'"'+(o.value===user.timezoneId?' selected':'')+'>'+escapeText(o.label)+'</option>'}).join('');return '<form class="asr-admin-editor" data-user-form><input type="hidden" name="userId" value="'+user.id+'"><section><h3>Identity & Account</h3><div class="asr-admin-grid">'+field('Name / callsign','name',user.name)+field('Email','email',user.email,'email')+field('Location','location',user.location)+'</div></section><section><h3>Permissions & Nodes</h3><p>Permissions control what this account can view or change. You cannot assign your own level or higher unless you are a superuser.</p><div class="asr-admin-grid"><label><span>Permission</span><select name="permission">'+permissions+'</select></label>'+field('Managed node numbers','nodes',(user.nodes||[]).join(' '))+'<label><span>Time zone</span><select name="timezoneId">'+zones+'</select></label></div></section><section><h3>Password & Security</h3>'+field(user.id?'New password (optional)':'Initial password','password','','password')+'</section><div class="asr-modal-actions">'+(user.id?'<button type="button" class="asr-destructive-action" data-delete-user>Delete User</button>':'')+'<button type="button" data-users-back>Cancel</button><button type="submit" class="asr-primary-action">'+(user.id?'Save User':'Add User')+'</button></div><p data-admin-status role="status"></p></form>'}
  function renderUsers(data){var body=document.querySelector('[data-admin-modal-body]');body.innerHTML='<div class="asr-user-list"><div class="asr-settings-section-lead"><div><h3>User Accounts</h3><p>Select an account to edit it. Your own account is managed under My Account.</p></div><button type="button" class="asr-primary-action" data-add-user>Add User</button></div>'+data.users.map(function(user){return '<button type="button" data-user-id="'+user.id+'"'+(user.isSelf?' disabled':'')+'><span><strong>'+escapeText(user.name)+'</strong><small>'+escapeText(user.email||'No email')+'</small></span><span>'+escapeText(user.permissionName)+(user.isSelf?' · You':'')+'</span></button>'}).join('')+'</div>';body.querySelector('[data-add-user]').addEventListener('click',function(){body.innerHTML=userEditor(data);bindUserForm(data)});body.querySelectorAll('[data-user-id]').forEach(function(button){button.addEventListener('click',function(){var selected=data.users.find(function(item){return item.id===Number(button.dataset.userId)});body.innerHTML=userEditor(data,selected);bindUserForm(data)})})}
  function bindUserForm(data){var body=document.querySelector('[data-admin-modal-body]');var editor=body.querySelector('[data-user-form]');editor.querySelector('[data-users-back]').addEventListener('click',function(){renderUsers(data)});var del=editor.querySelector('[data-delete-user]');if(del)del.addEventListener('click',function(){if(!confirm('Delete this user account? This cannot be undone.'))return;var values=new FormData();values.set('userId',editor.elements.userId.value);request('settings-user-delete',{method:'POST',body:values}).then(renderUsers).catch(function(error){editor.querySelector('[data-admin-status]').textContent=error.message})});editor.addEventListener('submit',function(event){event.preventDefault();request('settings-user-save',{method:'POST',body:new FormData(editor)}).then(renderUsers).catch(function(error){editor.querySelector('[data-admin-status]').textContent=error.message})})}
  function renderConfig(data){var c=data.config;var body=document.querySelector('[data-admin-modal-body]');body.innerHTML='<form class="asr-admin-editor" data-config-form><section><h3>Node Identity</h3><div class="asr-admin-grid">'+field('Call sign','call',c.call)+field('Node number','nodenum',c.nodenum)+field('Location','location',c.location)+field('Node title','title',c.title)+'</div></section><section><h3>Connection & Interface</h3><label class="asr-settings-check"><input type="checkbox" name="autodisc" value="1"'+(c.autodisc?' checked':'')+'><span>Disconnect before connecting by default</span></label><label class="asr-settings-check"><input type="checkbox" name="updatecheck" value="1"'+(c.updatecheck?' checked':'')+'><span>Check for AllScan updates</span></label><label class="asr-settings-check"><input type="checkbox" name="showcmdbuttons" value="1"'+(c.showcmdbuttons?' checked':'')+'><span>Show command buttons in Node Controls</span></label></section><details class="asr-progressive-details"><summary><span class="asr-disclosure-chevron">&gt;</span> Advanced</summary><div class="asr-admin-grid">'+field('AMI host','amiHost',c.amiHost)+field('AMI port','amiPort',c.amiPort)+field('AMI user','amiUser',c.amiUser)+field('AMI password','amiPassword','','password')+'<label><span>Favorites file locations</span><textarea name="favoritesLocations" rows="4">'+escapeText(c.favoritesLocations)+'</textarea></label><label><span>Custom command buttons</span><textarea name="commandButtons" rows="4">'+escapeText(c.commandButtons)+'</textarea></label></div></details><div class="asr-modal-actions"><button type="submit" class="asr-primary-action">Save Advanced Configuration</button></div><p data-admin-status role="status"></p></form>';var editor=body.querySelector('form');if(c.amiPasswordSaved)editor.elements.amiPassword.placeholder='Saved — leave blank to keep existing';editor.addEventListener('submit',function(event){event.preventDefault();request('settings-config-save',{method:'POST',body:new FormData(editor)}).then(function(next){renderConfig(next);body.querySelector('[data-admin-status]').textContent='Configuration saved.'}).catch(function(error){editor.querySelector('[data-admin-status]').textContent=error.message})})}
  function renderPerformance(data){if(!data||data.ok!==true||!data.load||!data.memory||!data.disk||typeof data.updated!=='string')throw new Error('Performance statistics did not match the expected response.');var target=document.querySelector('[data-performance-grid]');if(!target)return;var cards=[['CPU Temperature',data.cpuTemp],['System Load',data.load&&data.load.one],['Memory',data.memory&&data.memory.percent+'%'],['Disk',data.disk&&data.disk.percent+'%'],['Active ASR Viewers',data.activeViewers],['Asterisk',data.asteriskRunning?'Running':'Stopped'],['Status Cache',data.statusCacheAge==null?'Waiting':data.statusCacheAge+'s old'],['Node Uptime',data.uptime]];target.innerHTML=cards.map(function(card){return '<article><span>'+escapeText(card[0])+'</span><strong>'+escapeText(card[1]==null?'--':card[1])+'</strong></article>'}).join('');var updated=document.querySelector('[data-performance-updated]');if(updated)updated.textContent='Updated '+new Date(data.updated).toLocaleTimeString()}
  function loadPerformance(){request('performance-stats').then(renderPerformance).catch(function(error){var target=document.querySelector('[data-performance-grid]');if(target)target.innerHTML='<p class="asr-settings-warning">'+escapeText(error.message)+'</p>'})}
  function openAdmin(kind){var dialog=document.getElementById('asrAdminDialog');var body=dialog.querySelector('[data-admin-modal-body]');document.getElementById('asrAdminDialogTitle').textContent=kind==='users'?'Users':kind==='config'?'Advanced Configuration':'Performance Stats';body.innerHTML='<p>Loading…</p>';openModal(dialog);if(kind==='users')request('settings-users').then(renderUsers).catch(function(error){body.innerHTML='<p class="asr-settings-warning">'+escapeText(error.message)+'</p>'});if(kind==='config')request('settings-config').then(renderConfig).catch(function(error){body.innerHTML='<p class="asr-settings-warning">'+escapeText(error.message)+'</p>'});if(kind==='performance'){body.innerHTML='<p data-performance-updated>Loading current node information…</p><div class="asr-performance-grid" data-performance-grid></div>';loadPerformance();performanceTimer=setInterval(loadPerformance,4000)}}
  function saveLanguage(){var category=shell.dataset.settingsCategory;var labels={appearance:'Save Appearance Settings',bridges:'Save All Bridge Changes',lookup:'Save Lookup & Map Settings',access:'Save Access Settings'};if(labels[category])form.querySelectorAll('input[type="submit"][name="Submit"]').forEach(function(button){button.value=labels[category]})}
  function loadReleaseStatus(){var target=document.querySelector('[data-release-status]');if(!target)return;fetch(base+'/asr-api.php?action=release-status',{credentials:'same-origin',cache:'no-store'}).then(function(response){return response.json().then(function(data){if(!response.ok)throw new Error();return data})}).then(function(data){target.textContent=data.updateAvailable?'Update available':data.checked?'Up to date':'Unable to check'}).catch(function(){target.textContent='Unable to check'})}
  function dirtyState(){if(!form)return;var dirty=false;form.addEventListener('input',function(event){if(event.target.name&&!/^(urfAdminAction|ysfImportBridgeId)$/.test(event.target.name))dirty=true});form.addEventListener('change',function(){dirty=true});form.addEventListener('submit',function(){dirty=false});window.addEventListener('beforeunload',function(event){if(dirty){event.preventDefault();event.returnValue=''}})}

  compactNavigation();flattenSections();enhanceUrf();document.querySelectorAll('.asr-bridge-settings-row').forEach(enhanceBridgeRow)
  var table=document.querySelector('.asr-bridge-settings-table');if(table&&window.MutationObserver)new MutationObserver(function(){table.querySelectorAll('.asr-bridge-settings-row').forEach(enhanceBridgeRow)}).observe(table,{childList:true})
  var addBridge=document.querySelector('.asr-add-bridge-button');if(addBridge)addBridge.addEventListener('click',function(){setTimeout(function(){var rows=table&&table.querySelectorAll('.asr-bridge-settings-row');if(rows&&rows.length)openBridgeEditor(rows[rows.length-1])},0)})
  var addUrf=document.querySelector('.asr-add-urf-button');if(addUrf)addUrf.addEventListener('click',function(){setTimeout(function(){enhanceUrf();var row=document.querySelector('.asr-urf-settings-row');if(row)openBridgeEditor(row)},0)})
  document.querySelectorAll('[data-open-modal]').forEach(function(button){button.addEventListener('click',function(){openModal(document.getElementById(button.dataset.openModal))})})
  document.querySelectorAll('[data-open-admin-modal]').forEach(function(button){button.addEventListener('click',function(){openAdmin(button.dataset.openAdminModal)})})
  document.querySelectorAll('.asr-settings-modal .asr-modal-close').forEach(function(button){button.addEventListener('click',function(){var modal=button.closest('.asr-settings-modal');if(modal===bridgeEditor)closeBridgeEditor(true);else closeModal(modal)})})
  var cancel=bridgeEditor&&bridgeEditor.querySelector('[data-bridge-cancel]');if(cancel)cancel.addEventListener('click',function(){closeBridgeEditor(true)})
  document.addEventListener('keydown',function(event){if(event.key!=='Escape')return;if(activeBridge)closeBridgeEditor(true);else{var open=document.querySelector('.asr-settings-modal:not([hidden])');if(open)closeModal(open)}})
  loadDiagnostics();loadReleaseStatus();saveLanguage();dirtyState()
})()
