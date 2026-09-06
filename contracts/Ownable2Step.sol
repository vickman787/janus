// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @notice Minimal two step ownership transfer. Current owner proposes a new
/// owner. The proposed owner accepts. Ownership changes only after acceptance.
contract Ownable2Step {
    address private _owner;
    address private _pendingOwner;

    event OwnershipTransferStarted(address indexed previousOwner, address indexed newOwner);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    error OwnableUnauthorizedAccount(address account);
    error OwnablePendingOwnerNotZero(address pendingOwner);

    constructor(address initialOwner) {
        if (initialOwner == address(0)) {
            revert OwnableUnauthorizedAccount(initialOwner);
        }
        _owner = initialOwner;
        emit OwnershipTransferred(address(0), initialOwner);
    }

    /// @notice The current owner.
    function owner() public view returns (address) {
        return _owner;
    }

    /// @notice The address allowed to accept ownership, if any.
    function pendingOwner() public view returns (address) {
        return _pendingOwner;
    }

    /// @notice Propose a new owner. Only the current owner may call this.
    function transferOwnership(address newOwner) public {
        if (msg.sender != _owner) {
            revert OwnableUnauthorizedAccount(msg.sender);
        }
        if (newOwner == address(0)) {
            revert OwnableUnauthorizedAccount(newOwner);
        }
        _pendingOwner = newOwner;
        emit OwnershipTransferStarted(_owner, newOwner);
    }

    /// @notice Accept ownership. Only the pending owner may call this.
    function acceptOwnership() public {
        if (msg.sender != _pendingOwner) {
            revert OwnableUnauthorizedAccount(msg.sender);
        }
        address previousOwner = _owner;
        _owner = msg.sender;
        _pendingOwner = address(0);
        emit OwnershipTransferred(previousOwner, msg.sender);
    }
}
