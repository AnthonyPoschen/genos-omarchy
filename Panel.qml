import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "io.github.anthonyposchen.genos"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root
  readonly property string uiFont: bar ? bar.fontFamily : Style.font.family
  readonly property string helperPath: root.fileFromUrl(Qt.resolvedUrl("genos_panel.py"))
  readonly property int maxBody: Style.space(560)

  property var servers: []
  property int runningCount: 0
  property string status: ""
  property string operation: ""
  property string credentialSource: ""
  property bool needsLogin: false
  property string userCode: ""
  property string verificationUri: ""
  property string loginOrigin: ""
  property string confirmServerId: ""
  property string confirmAction: ""
  property string confirmActionLabel: ""
  property string confirmMessage: ""
  property string confirmSetupId: ""
  property string profileServerId: ""
  property var profileSetups: []
  property string profileSelectedSetupId: ""
  property string pendingInput: ""
  property string stdoutPending: ""
  property string stderrText: ""
  property bool dropped: false
  property bool sawResult: false
  property bool loginSaved: false
  property bool actionSaved: false
  property bool settingsOpen: false
  property string settingsMessage: ""

  function fileFromUrl(url) {
    var text = String(url)
    if (text.indexOf("file://") === 0) text = text.slice("file://".length)
    try { return decodeURIComponent(text) } catch (error) { return text }
  }

  function tooltipPlain(value) {
    return String(value === undefined || value === null ? "" : value)
      .replace(/[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/g, "")
      .replace(/[<>&]/g, "")
      .slice(0, 80)
  }

  function safeId(value) {
    var text = String(value || "")
    return /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(text) ? text : ""
  }

  function allowedUri(value) {
    var text = String(value || "")
    if (text.indexOf("<") >= 0 || text.indexOf(">") >= 0 || text.indexOf("&") >= 0) return false
    if (/^https:\/\/[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:\/[^\s]*)?$/.test(text)) return true
    return /^http:\/\/(localhost|127\.0\.0\.1|genos\.localhost)(?::[0-9]{1,5})?(?:\/[^\s]*)?$/.test(text)
  }

  function uriMatchesOrigin(uri, origin) {
    var page = String(uri || "")
    var base = String(origin || "")
    if (page === "" || base === "") return false
    return page === base || page.indexOf(base + "/") === 0 || page.indexOf(base + "?") === 0
  }

  function childEnvironment(includeToken) {
    var env = { "PATH": "/usr/bin", "LANG": "C.UTF-8" }
    var keys = ["HOME", "USER", "LOGNAME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "XDG_CONFIG_HOME"]
    for (var i = 0; i < keys.length; i++) {
      var value = String(Quickshell.env(keys[i]) || "")
      if (value.length > 0) env[keys[i]] = value
    }
    var host = String(root.setting("origin", "https://genosservers.com") || "").trim()
    if (host.length > 0) env["GENOS_HOST"] = host
    return env
  }

  function savedToken() {
    return String(root.setting("token", "") || "").trim()
  }

  function savedOrigin() {
    var origin = String(root.setting("origin", "https://genosservers.com") || "").trim()
    return origin.length > 0 ? origin : "https://genosservers.com"
  }

  function accountUrl() {
    var origin = root.savedOrigin().replace(/\/+$/, "")
    return origin + "/account#create-token"
  }

  function saveSetting(key, value) {
    var nextSettings = {}
    var existingKey
    for (existingKey in root.settings) nextSettings[existingKey] = root.settings[existingKey]
    nextSettings[key] = value
    root.settings = nextSettings
    if (root.hostWidget) root.hostWidget.settings = nextSettings
    if (root.bar && Util && Util.shellQuote) {
      root.bar.run("omarchy bar set " + Util.shellQuote(root.moduleName) + " " + Util.shellQuote(key) + " " + Util.shellQuote(JSON.stringify(value)) + " --json")
    }
  }

  function writeSettings() {
    root.pendingInput = JSON.stringify({ token: root.savedToken(), origin: root.savedOrigin() })
  }

  function openerEnvironment() {
    var env = root.childEnvironment(false)
    var keys = ["DISPLAY", "WAYLAND_DISPLAY", "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE", "XDG_DATA_DIRS", "XDG_CONFIG_DIRS"]
    for (var i = 0; i < keys.length; i++) {
      var value = String(Quickshell.env(keys[i]) || "")
      if (value.length > 0) env[keys[i]] = value
    }
    env["PATH"] = "/usr/local/bin:/usr/bin"
    return env
  }

  function scrubHelper() {
    helper.environment = ({ "PATH": "/usr/bin" })
    root.pendingInput = ""
    root.stdoutPending = ""
  }

  function stopHelper() {
    if (!helper.running) return
    helper.signal(15)
    killTimer.start()
  }

  function startHelper(args, includeToken) {
    if (helper.running || !args || args.length === 0) return false
    var command = ["/usr/bin/python3", "-I", "-S", root.helperPath]
    for (var i = 0; i < args.length; i++) command.push(String(args[i]))
    root.dropped = false
    root.sawResult = false
    root.stdoutPending = ""
    root.stderrText = ""
    root.operation = String(args[0])
    helper.command = command
    helper.clearEnvironment = true
    helper.environment = root.childEnvironment(false)
    if (includeToken) root.writeSettings()
    deadline.interval = args[0] === "login" ? 960000 : 20000
    deadline.restart()
    helper.running = true
    return true
  }

  function refresh() {
    if (!root.opened) return
    if (root.savedToken() === "") {
      root.needsLogin = true
      root.servers = []
      root.runningCount = 0
      root.credentialSource = ""
      return
    }
    root.needsLogin = false
    root.startHelper(["list", "--from-stdin"], true)
  }

  function toggleSettings() { root.settingsOpen = !root.settingsOpen }
  function openSettings() { root.settingsOpen = true }

  function signIn() {
    if (helper.running) return
    root.userCode = ""
    root.verificationUri = ""
    root.loginSaved = false
    root.status = "Opening Genos to sign in"
    root.startHelper(["login"], false)
  }

  function tryOpenUrlExternally(url) {
    try {
      return Qt.openUrlExternally(url) === true
    } catch (error) {
      return false
    }
  }

  function openWithBrowser(url) {
    if (!root.allowedUri(url) || opener.running) return
    if (root.tryOpenUrlExternally(url)) return
    opener.command = ["/usr/bin/xdg-open", url]
    opener.clearEnvironment = true
    opener.environment = root.openerEnvironment()
    opener.running = true
  }

  function openVerification() {
    root.openWithBrowser(root.verificationUri)
  }

  function saveOrigin() {
    var value = String(originField.text || "").trim().replace(/\/+$/, "")
    if (value === "") value = "https://genosservers.com"
    if (!root.allowedUri(value)) {
      root.settingsMessage = "Use https://genosservers.com, or a loopback address such as http://genos.localhost:8000."
      return
    }
    root.settingsMessage = ""
    root.saveSetting("origin", value)
    originField.text = value
  }

  function clearToken() {
    root.saveSetting("token", "")
    settingsTokenField.text = ""
    root.servers = []
    root.runningCount = 0
    root.needsLogin = true
    root.status = ""
    root.settingsMessage = "Token removed from this widget."
  }

  function openAccount() {
    root.openWithBrowser(root.accountUrl())
  }

  function clearConfirm() {
    root.confirmServerId = ""
    root.confirmAction = ""
    root.confirmActionLabel = ""
    root.confirmMessage = ""
    root.confirmSetupId = ""
  }

  function clearProfileChooser() {
    root.profileServerId = ""
    root.profileSetups = []
    root.profileSelectedSetupId = ""
  }

  function serverById(serverId) {
    var id = root.safeId(serverId)
    if (id === "" || !root.servers) return null
    for (var i = 0; i < root.servers.length && i < 64; i++) {
      if (root.servers[i] && root.servers[i].id === id) return root.servers[i]
    }
    return null
  }

  function requestAction(row, action) {
    if (!row || (action !== "start" && action !== "stop" && action !== "restart")) return
    var serverId = root.safeId(row.id)
    if (serverId === "") return
    root.clearProfileChooser()
    var ask = action === "stop" || (action === "restart" && row.restartNeedsConfirm === true)
    if (ask) {
      root.confirmServerId = serverId
      root.confirmAction = action
      root.confirmActionLabel = action === "restart" ? "Restart" : "Stop"
      var message = action === "stop" ? row.confirmStop : row.confirmRestart
      root.confirmMessage = root.tooltipPlain(message || ((action === "stop" ? "Stop " : "Restart ") + (row.tooltip || "this server") + "?"))
      return
    }
    root.commitAction(serverId, action, false)
  }

  function requestChangeProfile(row) {
    if (!row) return
    var serverId = root.safeId(row.id)
    if (serverId === "") return
    if (String(row.status || "") !== "Stopped") {
      root.status = "Stop the server before changing profile"
      return
    }
    root.clearConfirm()
    root.clearProfileChooser()
    root.status = ""
    root.startHelper(["setups", serverId, "--from-stdin"], true)
  }

  function requestSelectSetup(row, setup) {
    if (!row || !setup) return
    var serverId = root.safeId(row.id)
    var setupId = root.safeId(setup.id)
    if (serverId === "" || setupId === "") return
    if (String(row.status || "") !== "Stopped") {
      root.status = "Stop the server before changing profile"
      return
    }
    root.confirmServerId = serverId
    root.confirmAction = "select-setup"
    root.confirmSetupId = setupId
    root.confirmActionLabel = "Select"
    var profile = root.tooltipPlain(setup.name || "this profile")
    var serverName = root.tooltipPlain(row.tooltip || row.name || "this server")
    root.confirmMessage = root.tooltipPlain("Select " + profile + " on " + serverName + "?")
  }

  function requestUnloadSetup(row) {
    if (!row) return
    var serverId = root.safeId(row.id)
    if (serverId === "") return
    if (String(row.status || "") !== "Stopped") {
      root.status = "Stop the server before changing profile"
      return
    }
    root.confirmServerId = serverId
    root.confirmAction = "unload-setup"
    root.confirmSetupId = ""
    root.confirmActionLabel = "Unload"
    var serverName = root.tooltipPlain(row.tooltip || row.name || "this server")
    root.confirmMessage = root.tooltipPlain("Unload profile on " + serverName + "?")
  }

  function commitConfirm() {
    var serverId = root.safeId(root.confirmServerId)
    var action = root.confirmAction
    var setupId = root.safeId(root.confirmSetupId)
    root.clearConfirm()
    if (serverId === "") return
    if (action === "select-setup") {
      if (setupId === "") return
      root.commitSelectSetup(serverId, setupId, true)
      return
    }
    if (action === "unload-setup") {
      root.commitUnloadSetup(serverId, true)
      return
    }
    var lifecycle = action === "restart" ? "restart" : "stop"
    root.commitAction(serverId, lifecycle, true)
  }

  function commitAction(serverId, action, confirmed) {
    if (root.safeId(serverId) === "") return
    if (action !== "start" && action !== "stop" && action !== "restart") return
    var args = ["action", serverId, action]
    if (confirmed) args.push("--confirmed")
    root.actionSaved = false
    root.startHelper(args, true)
  }

  function commitSelectSetup(serverId, setupId, confirmed) {
    if (root.safeId(serverId) === "" || root.safeId(setupId) === "") return
    var args = ["select-setup", serverId, setupId]
    var expected = root.safeId(root.profileSelectedSetupId)
    if (expected !== "") {
      args.push("--expected")
      args.push(expected)
    }
    if (confirmed) args.push("--confirmed")
    root.actionSaved = false
    root.startHelper(args, true)
  }

  function commitUnloadSetup(serverId, confirmed) {
    if (root.safeId(serverId) === "") return
    var args = ["unload-setup", serverId]
    var expected = root.safeId(root.profileSelectedSetupId)
    if (expected !== "") {
      args.push("--expected")
      args.push(expected)
    }
    if (confirmed) args.push("--confirmed")
    root.actionSaved = false
    root.startHelper(args, true)
  }

  function detail(row) {
    var game = String(row.gameName || "")
    var state = String(row.status || "")
    var profile = String(row.selectedSetupName || "")
    var text = game
    if (state.length > 0) text = text.length > 0 ? text + " · " + state : state
    if (profile.length > 0) text = text.length > 0 ? text + " · " + profile : profile
    if (typeof row.playerCount === "number" && isFinite(row.playerCount)) {
      text += (text.length > 0 ? " · " : "") + row.playerCount + (row.playerCount === 1 ? " player" : " players")
    }
    return text
  }

  function countRunning(rows) {
    var count = 0
    if (!rows) return 0
    for (var i = 0; i < rows.length && i < 64; i++) {
      if (rows[i] && rows[i].status === "Running") count += 1
    }
    return count
  }

  function absorb(chunk, intoStderr) {
    if (root.dropped) return
    var piece = String(chunk || "")
    var current = intoStderr ? root.stderrText : root.stdoutPending
    if (current.length + piece.length > (intoStderr ? 4096 : 300000)) {
      root.dropped = true
      root.stdoutPending = ""
      root.stderrText = ""
      root.status = "Response was too large"
      root.stopHelper()
      return
    }
    if (intoStderr) {
      root.stderrText = root.tooltipPlain(current + piece)
      return
    }
    root.stdoutPending = current + piece
    var mark = root.stdoutPending.indexOf("\n")
    while (mark >= 0) {
      var line = root.stdoutPending.slice(0, mark)
      root.stdoutPending = root.stdoutPending.slice(mark + 1)
      root.takeLine(line)
      mark = root.stdoutPending.indexOf("\n")
    }
  }

  function takeLine(line) {
    if (!line || String(line).trim().length === 0) return
    var doc
    try { doc = JSON.parse(line) } catch (error) { return }
    if (!doc || typeof doc !== "object") return
    root.sawResult = true
    if (doc.event === "code") {
      root.userCode = root.tooltipPlain(doc.userCode).slice(0, 32)
      root.loginOrigin = String(doc.origin || "")
      var uri = String(doc.verificationUri || "")
      var uriOk = uri !== "" && root.allowedUri(uri) && root.uriMatchesOrigin(uri, root.loginOrigin)
      root.verificationUri = uriOk ? uri : ""
      if (uri !== "" && !uriOk) {
        root.status = root.userCode.length > 0
          ? ("Approval URL blocked — enter code " + root.userCode + " on the Genos account page")
          : "Approval URL blocked — open the Genos account page in your browser to approve"
      } else if (root.userCode.length > 0) {
        root.status = "Waiting for approval — code " + root.userCode
      } else {
        root.status = "Waiting for approval in the browser"
      }
      if (root.verificationUri !== "") root.openVerification()
      return
    }
    if (doc.event === "session" && doc.token) {
      root.saveSetting("token", String(doc.token))
      root.loginSaved = true
      root.needsLogin = false
      root.status = "Signed in"
      return
    }
    if (doc.event === "stored" || (doc.ok === true && doc.stored)) {
      root.loginSaved = true
      return
    }
    if (doc.needsConfirm === true) {
      root.confirmServerId = root.safeId(doc.serverId)
      if (doc.action === "select-setup") {
        root.confirmAction = "select-setup"
        root.confirmActionLabel = "Select"
        root.confirmSetupId = root.safeId(doc.setupId)
      } else if (doc.action === "unload-setup") {
        root.confirmAction = "unload-setup"
        root.confirmActionLabel = "Unload"
        root.confirmSetupId = ""
      } else {
        root.confirmAction = doc.action === "restart" ? "restart" : "stop"
        root.confirmActionLabel = root.confirmAction === "restart" ? "Restart" : "Stop"
        root.confirmSetupId = ""
      }
      root.confirmMessage = root.tooltipPlain(doc.message)
      root.status = ""
      return
    }
    if (doc.ok === true && doc.servers !== undefined) {
      var rows = Array.isArray(doc.servers) ? doc.servers.slice(0, 64) : []
      var keep = false
      for (var i = 0; i < rows.length; i++) {
        if (rows[i] && rows[i].id === root.confirmServerId) keep = true
      }
      if (!keep) root.clearConfirm()
      if (root.profileServerId !== "") {
        var keepProfile = false
        for (var j = 0; j < rows.length; j++) {
          if (rows[j] && rows[j].id === root.profileServerId && rows[j].status === "Stopped") keepProfile = true
        }
        if (!keepProfile) root.clearProfileChooser()
      }
      root.servers = rows
      root.runningCount = root.countRunning(rows)
      root.needsLogin = false
      root.credentialSource = ""
      root.status = rows.length === 0 ? "No servers on this account." : ""
      return
    }
    if (doc.ok === true && doc.setups !== undefined) {
      root.profileServerId = root.safeId(doc.serverId)
      root.profileSetups = Array.isArray(doc.setups) ? doc.setups.slice(0, 64) : []
      root.profileSelectedSetupId = root.safeId(doc.selectedSetupID)
      root.status = root.profileSetups.length === 0 ? "No profiles on this server." : ""
      return
    }
    if (doc.ok === true && (doc.action === "start" || doc.action === "stop" || doc.action === "restart")) {
      root.actionSaved = true
      root.status = "Requested " + doc.action
      return
    }
    if (doc.ok === true && (doc.action === "select-setup" || doc.action === "unload-setup")) {
      root.actionSaved = true
      root.clearProfileChooser()
      root.clearConfirm()
      root.status = doc.action === "unload-setup" ? "Unloaded profile" : "Selected profile"
      return
    }
    if (doc.ok === false || doc.error) {
      if (doc.error === "credentials") {
        root.needsLogin = true
        root.servers = []
        root.runningCount = 0
        root.clearProfileChooser()
      }
      root.status = root.tooltipPlain(doc.message || "Request failed")
    }
  }

  function finishHelper() {
    deadline.stop()
    killTimer.stop()
    if (!root.sawResult && !root.dropped && root.stderrText.length > 0) root.status = root.stderrText
    var savedLogin = root.loginSaved
    var savedAction = root.actionSaved
    root.loginSaved = false
    root.actionSaved = false
    root.scrubHelper()
    if (root.opened && (savedLogin || savedAction)) Qt.callLater(root.refresh)
  }

  onOpenedChanged: {
    if (root.opened) {
      root.settingsOpen = false
      if (root.savedToken() === "") {
        root.needsLogin = true
        root.status = "Authentication not configured"
      } else root.refresh()
    } else {
      root.pendingInput = ""
      root.stopHelper()
    }
  }
  Component.onDestruction: {
    root.stopHelper()
    root.scrubHelper()
  }

  function open() { root.controller.show() }
  function close() { root.controller.hide() }

  Timer {
    id: deadline
    onTriggered: root.stopHelper()
  }
  Timer {
    id: killTimer
    interval: 2000
    onTriggered: { if (helper.running) helper.signal(9) }
  }
  Timer {
    interval: 30000
    repeat: true
    running: root.opened && root.operation !== "login" && !helper.running
    onTriggered: root.refresh()
  }

  Process {
    id: helper
    running: false
    stdinEnabled: true
    clearEnvironment: true
    command: []
    workingDirectory: String(Quickshell.env("HOME") || "/")
    environment: ({ "PATH": "/usr/bin" })
    onStarted: {
      if (root.pendingInput.length > 0 && (root.operation === "list" || root.operation === "action" || root.operation === "setups" || root.operation === "select-setup" || root.operation === "unload-setup")) {
        var chunk = root.pendingInput
        root.pendingInput = ""
        helper.write(chunk + "\n")
      } else {
        root.pendingInput = ""
      }
    }
    onExited: function(_code, _status) { root.finishHelper() }
    stdout: SplitParser {
      splitMarker: ""
      onRead: function(chunk) { root.absorb(chunk, false) }
    }
    stderr: SplitParser {
      splitMarker: ""
      onRead: function(chunk) { root.absorb(chunk, true) }
    }
  }

  Process {
    id: opener
    running: false
    clearEnvironment: true
    command: []
    environment: ({ "PATH": "/usr/bin" })
    onExited: function(code, _status) {
      if (code === 0) return
      var cmd = opener.command
      var opened = cmd && cmd.length > 1 ? String(cmd[1] || "") : ""
      if (opened !== "" && opened === root.verificationUri) {
        var fail = "Could not open the browser"
        if (root.userCode.length > 0) fail += ". Enter code " + root.userCode
        if (root.verificationUri !== "") fail += " or use Open again"
        root.status = fail
        return
      }
      if (root.settingsOpen) {
        root.settingsMessage = "Could not open the Genos account page"
        return
      }
      root.status = "Could not open the browser"
    }
  }

  KeyboardPanel {
    id: popup
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: catcher
    contentWidth: popup.fittedContentWidth(Style.space(440))
    contentHeight: popup.fittedContentHeight(Math.min(layout.implicitHeight, root.maxBody))

    PanelKeyCatcher {
      id: catcher
      anchors.fill: parent
      blocked: false
      onCloseRequested: {
        if (root.confirmServerId !== "") root.clearConfirm()
        else if (root.profileServerId !== "") root.clearProfileChooser()
        else root.close()
      }
      onTabRequested: function(direction) { if (root.bar && root.bar.switchPanelFrom) root.bar.switchPanelFrom(root.barIdentity, direction) }

      Flickable {
        id: scroller
        width: parent.width
        height: Math.min(layout.implicitHeight, root.maxBody)
        contentWidth: width
        contentHeight: layout.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        Column {
          id: layout
          width: scroller.width
          spacing: Style.space(10)

          PlainText {
            width: parent.width
            text: "Genos"
            color: root.barForeground
            font.family: root.uiFont
            font.pixelSize: Style.font.title
            font.bold: true
          }
          Column {
            width: parent.width
            visible: root.status !== ""
            spacing: Style.space(2)
            PlainText {
              width: parent.width
              text: "Status"
              color: root.barForeground
              opacity: 0.52
              font.family: root.uiFont
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 0.8
            }
            PlainText {
              width: parent.width
              text: root.status
              wrapMode: Text.WordWrap
              color: root.barForeground
              opacity: 0.72
              font.family: root.uiFont
              font.pixelSize: Style.font.bodySmall
            }
          }

          Column {
            width: parent.width
            visible: root.needsLogin
            spacing: Style.space(8)
            Button {
              text: helper.running && root.operation === "login" ? "Signing in…" : "Sign in"
              bordered: true
              fontFamily: root.uiFont
              foreground: root.barForeground
              enabled: !(helper.running && root.operation === "login")
              onClicked: root.signIn()
            }
            PlainText {
              width: parent.width
              visible: root.userCode.length > 0
              text: "Your code: " + root.userCode
              wrapMode: Text.WordWrap
              color: root.barForeground
              font.family: root.uiFont
              font.pixelSize: Style.font.body
              font.bold: true
            }
            TextEdit {
              width: parent.width
              visible: root.verificationUri !== ""
              text: root.verificationUri
              readOnly: true
              selectByMouse: true
              wrapMode: TextEdit.WrapAnywhere
              textFormat: TextEdit.PlainText
              color: root.barForeground
              opacity: 0.72
              font.family: root.uiFont
              font.pixelSize: Style.font.caption
            }
            Button {
              visible: root.verificationUri !== ""
              text: "Open again"
              bordered: true
              fontFamily: root.uiFont
              foreground: root.barForeground
              enabled: !opener.running
              onClicked: root.openVerification()
            }
          }

          Repeater {
            model: root.servers
            delegate: Column {
              required property var modelData
              width: parent.width
              spacing: Style.space(4)

              Item {
                width: parent.width
                height: nameText.implicitHeight
                PlainText {
                  id: nameText
                  width: parent.width
                  text: String(modelData.name || "")
                  elide: Text.ElideRight
                  color: root.barForeground
                  font.family: root.uiFont
                  font.pixelSize: Style.font.body
                  font.bold: true
                }
                MouseArea {
                  id: nameHover
                  anchors.fill: parent
                  hoverEnabled: true
                  acceptedButtons: Qt.NoButton
                }
                PanelToolTip {
                  visible: nameHover.containsMouse
                  text: root.tooltipPlain(modelData.tooltip || modelData.name)
                  fontFamily: root.uiFont
                }
              }
              PlainText {
                width: parent.width
                text: root.detail(modelData)
                elide: Text.ElideRight
                color: root.barForeground
                opacity: 0.72
                font.family: root.uiFont
                font.pixelSize: Style.font.bodySmall
              }
              Row {
                spacing: Style.space(6)
                visible: root.confirmServerId !== modelData.id && root.profileServerId !== modelData.id
                Button {
                  text: "Start"
                  bordered: true
                  fontFamily: root.uiFont
                  foreground: root.barForeground
                  enabled: !helper.running
                  onClicked: root.requestAction(modelData, "start")
                }
                Button {
                  text: "Stop"
                  bordered: true
                  fontFamily: root.uiFont
                  foreground: root.barForeground
                  enabled: !helper.running
                  onClicked: root.requestAction(modelData, "stop")
                }
                Button {
                  text: "Restart"
                  bordered: true
                  fontFamily: root.uiFont
                  foreground: root.barForeground
                  enabled: !helper.running
                  onClicked: root.requestAction(modelData, "restart")
                }
                Button {
                  text: "Change profile"
                  bordered: true
                  fontFamily: root.uiFont
                  foreground: root.barForeground
                  visible: modelData.status === "Stopped"
                  enabled: !helper.running
                  onClicked: root.requestChangeProfile(modelData)
                }
              }
              Column {
                width: parent.width
                visible: root.profileServerId === modelData.id && root.confirmServerId !== modelData.id
                spacing: Style.space(6)
                PlainText {
                  width: parent.width
                  text: "Choose a profile"
                  color: root.barForeground
                  font.family: root.uiFont
                  font.pixelSize: Style.font.body
                  font.bold: true
                }
                Repeater {
                  model: root.profileSetups
                  delegate: Button {
                    required property var modelData
                    text: String(modelData.name || modelData.id || "profile") + (modelData.selected === true ? " (selected)" : "")
                    bordered: true
                    fontFamily: root.uiFont
                    foreground: root.barForeground
                    enabled: !helper.running && modelData.selected !== true
                    onClicked: root.requestSelectSetup(root.serverById(root.profileServerId), modelData)
                  }
                }
                Row {
                  spacing: Style.space(6)
                  Button {
                    text: "Unload"
                    bordered: true
                    fontFamily: root.uiFont
                    foreground: root.barForeground
                    visible: root.profileSelectedSetupId !== ""
                    enabled: !helper.running
                    onClicked: root.requestUnloadSetup(root.serverById(root.profileServerId))
                  }
                  Button {
                    text: "Cancel"
                    bordered: true
                    fontFamily: root.uiFont
                    foreground: root.barForeground
                    onClicked: root.clearProfileChooser()
                  }
                }
              }
              Column {
                width: parent.width
                visible: root.confirmServerId === modelData.id
                spacing: Style.space(6)
                PlainText {
                  width: parent.width
                  text: root.confirmMessage
                  wrapMode: Text.WordWrap
                  color: root.barForeground
                  font.family: root.uiFont
                  font.pixelSize: Style.font.body
                }
                Row {
                  spacing: Style.space(6)
                  Button {
                    text: "Cancel"
                    bordered: true
                    fontFamily: root.uiFont
                    foreground: root.barForeground
                    onClicked: root.clearConfirm()
                  }
                  Button {
                    text: root.confirmActionLabel
                    bordered: true
                    fontFamily: root.uiFont
                    foreground: root.barForeground
                    enabled: !helper.running
                    onClicked: root.commitConfirm()
                  }
                }
              }
            }
          }
        }
      }
    }
  }

  KeyboardPanel {
    id: settingsPopup
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.settingsOpen
    focusTarget: settingsCatcher
    contentWidth: settingsPopup.fittedContentWidth(Style.space(430))
    contentHeight: settingsPopup.fittedContentHeight(settingsContent.implicitHeight)

    PanelKeyCatcher {
      id: settingsCatcher
      anchors.fill: parent
      blocked: originField.activeFocus || settingsTokenField.activeFocus
      onCloseRequested: root.settingsOpen = false

      Column {
        id: settingsContent
        width: parent.width
        spacing: Style.space(10)

        Item {
          width: parent.width
          height: settingsTitle.implicitHeight
          PlainText {
            id: settingsTitle
            text: "Settings"
            color: root.barForeground
            font.family: root.uiFont
            font.pixelSize: Style.font.title
            font.bold: true
          }
          PlainText {
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            text: "Right-click to close"
            color: root.barForeground
            opacity: 0.52
            font.family: root.uiFont
            font.pixelSize: Style.font.caption
          }
        }

        PlainText {
          width: parent.width
          text: "Genos site"
          color: root.barForeground
          font.family: root.uiFont
          font.pixelSize: Style.font.bodySmall
        }
        TextField {
          id: originField
          width: parent.width
          text: root.savedOrigin()
          placeholderText: "https://genosservers.com"
          font.family: root.uiFont
          foreground: root.barForeground
          selectByMouse: true
          onAccepted: root.saveOrigin()
        }
        Button {
          text: "Save site"
          bordered: true
          fontFamily: root.uiFont
          foreground: root.barForeground
          onClicked: root.saveOrigin()
        }

        PlainText {
          width: parent.width
          text: root.savedToken() === "" ? "No token saved yet." : "A token is saved with this widget."
          wrapMode: Text.WordWrap
          color: root.barForeground
          font.family: root.uiFont
          font.pixelSize: Style.font.body
        }
        TextField {
          id: settingsTokenField
          width: parent.width
          password: true
          maximumLength: 4096
          placeholderText: "Replace personal access token"
          font.family: root.uiFont
          foreground: root.barForeground
          onAccepted: {
            var value = String(text || "").trim()
            text = ""
            if (value === "") return
            root.saveSetting("token", value)
            root.needsLogin = false
            root.settingsMessage = "Token saved."
          }
        }
        Row {
          spacing: Style.space(6)
          Button {
            text: "Create a token"
            bordered: true
            fontFamily: root.uiFont
            foreground: root.barForeground
            onClicked: root.openAccount()
          }
          Button {
            text: "Remove token"
            bordered: true
            fontFamily: root.uiFont
            foreground: root.barForeground
            enabled: root.savedToken() !== ""
            onClicked: root.clearToken()
          }
        }
        PlainText {
          width: parent.width
          visible: root.settingsMessage !== ""
          text: root.settingsMessage
          wrapMode: Text.WordWrap
          color: root.barForeground
          opacity: 0.72
          font.family: root.uiFont
          font.pixelSize: Style.font.bodySmall
        }
      }

      MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.RightButton
        z: 1
        onPressed: root.settingsOpen = false
      }
    }
  }
}
