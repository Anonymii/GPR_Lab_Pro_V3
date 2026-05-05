import QtQuick
import QtQml
import QtQuick.Controls
import QtLocation
import QtPositioning

Rectangle {
    id: root
    color: "#f7f9fc"
    property real sceneMinLat: 35.75
    property real sceneMinLon: 120.05
    property real sceneMaxLat: 35.95
    property real sceneMaxLon: 120.25
    property bool mapReadySent: false

    function supportedMapTypesSummary() {
        const parts = []
        for (let i = 0; i < map.supportedMapTypes.length; ++i) {
            const item = map.supportedMapTypes[i]
            parts.push(i + ":" + item.name + "/style=" + item.style)
        }
        return parts.join(", ")
    }

    function ensureMapType() {
        if (!overviewBridge.offlineTileHost || map.supportedMapTypes.length === 0) {
            return;
        }
        const targetType = map.supportedMapTypes[map.supportedMapTypes.length - 1]
        if (map.activeMapType !== targetType) {
            console.log("Offline map selecting custom map type:", targetType.name,
                        "style=", targetType.style,
                        "supported=", root.supportedMapTypesSummary())
            map.activeMapType = targetType
        }
    }

    function notifyCurrentMapState() {
        overviewBridge.notifyMapState(map.center.latitude, map.center.longitude, map.zoomLevel)
    }

    function scheduleMapStateNotify() {
        mapStateNotifyTimer.restart()
    }

    function fitSceneBounds() {
        if (!overviewBridge.offlineTileHost || sceneMinLat >= sceneMaxLat || sceneMinLon >= sceneMaxLon) {
            return;
        }
        map.visibleRegion = QtPositioning.rectangle(
            QtPositioning.coordinate(sceneMaxLat, sceneMinLon),
            QtPositioning.coordinate(sceneMinLat, sceneMaxLon)
        )
        root.scheduleMapStateNotify()
    }

    Plugin {
        id: mapPlugin
        name: "osm"
        PluginParameter {
            name: "osm.mapping.custom.host"
            value: overviewBridge.offlineTileHost
        }
        PluginParameter {
            name: "osm.mapping.providersrepository.disabled"
            value: true
        }
    }

    Connections {
        target: overviewBridge
        function onOfflineTileHostChanged() {
            console.log("Offline map host changed:", overviewBridge.offlineTileHost)
            root.ensureMapType()
            Qt.callLater(root.fitSceneBounds)
        }
    }

    Timer {
        id: mapStateNotifyTimer
        interval: 0
        repeat: false
        onTriggered: root.notifyCurrentMapState()
    }

    Map {
        id: map
        anchors.fill: parent
        plugin: mapPlugin
        activeMapType: supportedMapTypes.length > 0 ? supportedMapTypes[supportedMapTypes.length - 1] : null
        center: QtPositioning.coordinate((root.sceneMinLat + root.sceneMaxLat) * 0.5, (root.sceneMinLon + root.sceneMaxLon) * 0.5)
        zoomLevel: Math.min(Math.max(14, overviewBridge.offlineMinZoom), overviewBridge.offlineMaxZoom)
        minimumZoomLevel: overviewBridge.offlineMinZoom
        maximumZoomLevel: overviewBridge.offlineMaxZoom

        Component.onCompleted: {
            console.log("Offline map completed. host=", overviewBridge.offlineTileHost,
                        " supportedMapTypes=", supportedMapTypes.length,
                        " summary=", root.supportedMapTypesSummary(),
                        " minZoom=", overviewBridge.offlineMinZoom,
                        " maxZoom=", overviewBridge.offlineMaxZoom)
            if (supportedMapTypes.length === 0) {
                console.warn("Offline map has no supported map types")
            }
            if (!root.mapReadySent) {
                root.mapReadySent = true
                overviewBridge.notifyMapReady()
                root.ensureMapType()
                Qt.callLater(root.fitSceneBounds)
            }
        }

        onSupportedMapTypesChanged: {
            console.log("Offline map supportedMapTypes changed:", supportedMapTypes.length,
                        " summary=", root.supportedMapTypesSummary())
            if (supportedMapTypes.length === 0) {
                console.warn("Offline map supportedMapTypes became empty")
            }
            root.ensureMapType()
        }

        onActiveMapTypeChanged: {
            if (activeMapType) {
                console.log("Offline map activeMapType changed:", activeMapType.name,
                            "style=", activeMapType.style)
            }
        }

        onCenterChanged: {
            root.scheduleMapStateNotify()
        }

        onZoomLevelChanged: {
            root.scheduleMapStateNotify()
        }

        PinchHandler {
            id: pinch
            target: null
            onActiveChanged: if (active) {
                map.startCentroid = map.toCoordinate(pinch.centroid.position, false)
            }
            onScaleChanged: (delta) => {
                map.zoomLevel += Math.log2(delta)
                map.alignCoordinateToPoint(map.startCentroid, pinch.centroid.position)
            }
            grabPermissions: PointerHandler.TakeOverForbidden
        }

        WheelHandler {
            id: wheel
            acceptedDevices: Qt.platform.pluginName === "cocoa" || Qt.platform.pluginName === "wayland"
                             ? PointerDevice.Mouse | PointerDevice.TouchPad
                             : PointerDevice.Mouse
            rotationScale: 1 / 120
            property: "zoomLevel"
        }

        DragHandler {
            id: drag
            target: null
            onTranslationChanged: (delta) => map.pan(-delta.x, -delta.y)
        }

        TapHandler {
            acceptedButtons: Qt.LeftButton
            onTapped: (point) => overviewBridge.notifyMapTapped(point.position.x, point.position.y)
        }
    }
}
