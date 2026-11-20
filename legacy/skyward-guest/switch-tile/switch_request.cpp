// SPDX-License-Identifier: Apache-2.0
#include <jni.h>
#include <sys/system_properties.h>

extern "C" JNIEXPORT jboolean JNICALL
Java_org_skyward_switchtile_SwitchTileService_requestSwitch(JNIEnv*, jclass) {
    return __system_property_set("vendor.skyward.switch_requested", "1") == 0
            ? JNI_TRUE : JNI_FALSE;
}
