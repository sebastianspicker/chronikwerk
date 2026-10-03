/** Drive the dependency-free admin UI: CSRF, reauthentication, and staged config edits. */

import { initAutoSubmit, initDialogClose, initPendingForms } from './admin/boot';
import { initConfigForm } from './admin/config';
import { initOverview, initStorageCheck } from './admin/overview';
import { initReauthForm } from './admin/reauth';

initAutoSubmit();
initDialogClose();
initPendingForms();
initReauthForm();
initStorageCheck();
initOverview();
initConfigForm();
