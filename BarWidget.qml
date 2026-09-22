import QtQuick
import qs.Commons
import qs.Ui

BarWidget {
  id: root
  moduleName: "io.github.anthonyposchen.genos"
  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false
  readonly property int runningCount: panelLoader.item ? panelLoader.item.runningCount : 0

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    target.bar = root.bar
    target.settings = root.settings
    target.anchorItem = button
    target.hostWidget = root
  }

  function refresh() { if (panelLoader.item) panelLoader.item.refresh() }
  function open() { if (panelLoader.item) panelLoader.item.open() }
  function close() { if (panelLoader.item) panelLoader.item.close() }
  function closeForPopoutSwitch() { if (panelLoader.item) panelLoader.item.closeForPopoutSwitch() }
  function togglePanel() { if (panelLoader.item) panelLoader.item.toggle() }
  function toggleSettings() { if (panelLoader.item) panelLoader.item.toggleSettings() }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight
  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: { root.injectPanel(); Qt.callLater(root.injectPanel) }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    iconComponent: logoMark
    active: root.opened || root.runningCount > 0
    useActiveColor: false
    tooltipText: root.runningCount > 0
      ? ("Genos · " + root.runningCount + (root.runningCount === 1 ? " server running" : " servers running") + "\nLeft-click servers · right-click settings")
      : "Genos\nLeft-click servers · right-click settings"
    onPressed: function(pressedButton) {
      if (pressedButton === Qt.MiddleButton) root.refresh()
      else if (pressedButton === Qt.RightButton) root.toggleSettings()
      else root.togglePanel()
    }
  }

  Component {
    id: logoMark
    Image {
      anchors.fill: parent
      source: Qt.resolvedUrl("assets/genos-logo.webp")
      fillMode: Image.PreserveAspectFit
      smooth: true
      mipmap: true
    }
  }
}
