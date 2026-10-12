export class ApiError extends Error {
  constructor(message, status, payload = {}) {
    super(message);
    this.status = status;
    this.offline = status === undefined || status === null;
    this.retryable = this.offline || status >= 500 || status === 408 || status === 429;
    this.applied = payload.applied || [];
    this.failed_op_id = payload.failed_op_id;
    this.failed_index = payload.failed_index;
  }
}
