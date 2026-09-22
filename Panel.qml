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

  function submitToken() {
    var value = String(tokenField.text || "").trim()
    tokenField.text = ""
    if (!value) {
      root.status = "Paste a personal access token."
      return
    }
    root.saveSetting("token", value)
    root.needsLogin = false
    root.status = ""
    root.refresh()
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
    tokenField.text = ""
    settingsTokenField.text = ""
    root.servers = []
    root.runningCount = 0
    root.needsLogin = true
    root.status = ""
    root.settingsMessage = "Token removed from this widget."
  }

  function openAccount() {
    var url = root.accountUrl()
    if (!root.allowedUri(url) || opener.running) return
    opener.command = ["/usr/bin/xdg-open", "--", url]
    opener.clearEnvironment = true
    opener.environment = root.openerEnvironment()
    opener.running = true
  }

  function clearConfirm() {
    root.confirmServerId = ""
    root.confirmAction = ""
    root.confirmActionLabel = ""
    root.confirmMessage = ""
  }

  function requestAction(row, action) {
    if (!row || (action !== "start" && action !== "stop" && action !== "restart")) return
    var serverId = root.safeId(row.id)
    if (serverId === "") return
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

  function commitConfirm() {
    var serverId = root.safeId(root.confirmServerId)
    var action = root.confirmAction === "restart" ? "restart" : "stop"
    root.clearConfirm()
    if (serverId === "") return
    root.commitAction(serverId, action, true)
  }

  function commitAction(serverId, action, confirmed) {
    if (root.safeId(serverId) === "") return
    if (action !== "start" && action !== "stop" && action !== "restart") return
    var args = ["action", serverId, action]
    if (confirmed) args.push("--confirmed")
    root.actionSaved = false
    root.startHelper(args, true)
  }

  function detail(row) {
    var game = String(row.gameName || "")
    var state = String(row.status || "")
    var text = game
    if (state.length > 0) text = text.length > 0 ? text + " · " + state : state
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
      root.verificationUri = root.allowedUri(uri) && root.uriMatchesOrigin(uri, root.loginOrigin) ? uri : ""
      root.status = root.userCode.length > 0 ? "Approve " + root.userCode : "Approve the device login"
      return
    }
    if (doc.event === "stored" || (doc.ok === true && doc.stored)) {
      root.loginSaved = true
      root.needsLogin = false
      root.status = doc.where === "file" || doc.stored === "file"
        ? "Saved the token in the credentials file because the keyring was unavailable."
        : "Saved the token in the keyring."
      return
    }
    if (doc.needsConfirm === true) {
      root.confirmServerId = root.safeId(doc.serverId)
      root.confirmAction = doc.action === "restart" ? "restart" : "stop"
      root.confirmActionLabel = root.confirmAction === "restart" ? "Restart" : "Stop"
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
      root.servers = rows
      root.runningCount = root.countRunning(rows)
      root.needsLogin = false
      root.credentialSource = ""
      root.status = rows.length === 0 ? "No servers on this account." : ""
      return
    }
    if (doc.ok === true && (doc.action === "start" || doc.action === "stop" || doc.action === "restart")) {
      root.actionSaved = true
      root.status = "Requested " + doc.action
      return
    }
    if (doc.ok === false || doc.error) {
      if (doc.error === "credentials") {
        root.needsLogin = true
        root.servers = []
        root.runningCount = 0
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
        root.status = ""
        Qt.callLater(function() { tokenField.forceActiveFocus() })
      } else root.refresh()
    } else {
      tokenField.text = ""
      root.pendingInput = ""
      root.stopHelper()
    }
  }
  Component.onDestruction: {
    tokenField.text = ""
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
      if (root.pendingInput.length > 0 && (root.operation === "list" || root.operation === "action")) {
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
      blocked: tokenField.activeFocus
      onCloseRequested: {
        if (root.confirmServerId !== "") root.clearConfirm()
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
          PlainText {
            width: parent.width
            visible: root.status !== ""
            text: root.status
            wrapMode: Text.WordWrap
            color: root.barForeground
            font.family: root.uiFont
            font.pixelSize: Style.font.body
          }

          Column {
            width: parent.width
            visible: root.needsLogin
            spacing: Style.space(8)
            PlainText {
              width: parent.width
              text: "Paste a personal access token. It is saved with this bar widget."
              wrapMode: Text.WordWrap
              color: root.barForeground
              font.family: root.uiFont
              font.pixelSize: Style.font.body
            }
            TextField {
              id: tokenField
              width: parent.width
              password: true
              maximumLength: 4096
              placeholderText: "Personal access token"
              font.family: root.uiFont
              foreground: root.barForeground
              onAccepted: root.submitToken()
            }
            Button {
              text: "Save token"
              bordered: true
              fontFamily: root.uiFont
              foreground: root.barForeground
              enabled: !helper.running
              onClicked: root.submitToken()
            }
            Button {
              text: "Create a token"
              bordered: true
              fontFamily: root.uiFont
              foreground: root.barForeground
              onClicked: root.openAccount()
            }
            PlainText {
              width: parent.width
              text: "Opens the account page so you can create a token, then paste it here."
              wrapMode: Text.WordWrap
              color: root.barForeground
              opacity: 0.62
              font.family: root.uiFont
              font.pixelSize: Style.font.bodySmall
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
                visible: root.confirmServerId !== modelData.id
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
