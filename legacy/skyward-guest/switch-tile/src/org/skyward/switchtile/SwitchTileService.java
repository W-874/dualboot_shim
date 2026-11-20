// SPDX-License-Identifier: Apache-2.0
package org.skyward.switchtile;

import android.app.AlertDialog;
import android.os.UserManager;
import android.service.quicksettings.Tile;
import android.service.quicksettings.TileService;
import android.widget.Toast;

/** Offers the system user a confirmed switch back to the installed OS. */
public final class SwitchTileService extends TileService {
    static {
        System.loadLibrary("skyward_switch_tile_jni");
    }

    private static native boolean requestSwitch();

    private boolean isSystemUser() {
        UserManager users = getSystemService(UserManager.class);
        return users != null && users.isSystemUser();
    }

    @Override
    public void onStartListening() {
        Tile tile = getQsTile();
        if (tile == null) return;
        tile.setState(isSystemUser() ? Tile.STATE_INACTIVE : Tile.STATE_UNAVAILABLE);
        tile.updateTile();
    }

    @Override
    public void onClick() {
        if (!isSystemUser()) return;
        if (isLocked()) {
            unlockAndRun(this::confirmSwitch);
        } else {
            confirmSwitch();
        }
    }

    private void confirmSwitch() {
        if (!isSystemUser() || isLocked()) return;
        showDialog(new AlertDialog.Builder(this)
                .setTitle(R.string.tile_label)
                .setMessage(R.string.switch_message)
                .setNegativeButton(android.R.string.cancel, null)
                .setPositiveButton(R.string.switch_confirm, (dialog, which) -> {
                    if (!isSystemUser() || isLocked()) return;
                    if (!requestSwitch()) {
                        Toast.makeText(this, R.string.switch_failed, Toast.LENGTH_LONG).show();
                    }
                })
                .create());
    }
}
